<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# Token Saver Router — Architecture

- 문서 목적: token_saver의 system architecture를 정의한다. Component / Request lifecycle / Data layer / Multi-user isolation / Deployment.
- 범위: HTTP proxy 구조, request 처리 순서, Redis/Mongo 역할 분담, multi-user 격리 경계
- 대상 독자: 구현자, 운영자, AI 에이전트, 후속 세션
- 상태: draft (TASK-001 lock-in 후, TASK-002 구현 진입 직전)
- 최종 수정일: 2026-06-24
- 관련 문서: [Concept](./token-saver-concept.md), [Project Profile](../PROJECT_PROFILE.md), [Session Handoff](../../ai-workflow/memory/active/session_handoff.md)

## 1. 시스템 개요

### 1.1 핵심 컨셉 (Concept §1 재확인)

> OpenAI-compatible HTTP proxy에서 들어온 요청을 **content type과 policy**로 보고, 어떻게 처리할지 결정하는 **policy engine**. mode별로 lossless(line routing) 또는 lossy(compression)를 선택하고, 결과를 provider에 전달.

### 1.2 핵심 결정 (TASK-001 lock-in)

| 결정 | 선택 | 근거 |
|---|---|---|
| 언어 | Python-only (1차) | headroom 32k LOC는 ML+6 compressor가 필요했던 규모, 우리 P0는 policy engine에 집중 |
| Storage | Redis (hot) + Mongo (cold) | multi-user 가정, TTL 적합 data는 Redis, 영구+queryable data는 Mongo |
| Multi-user | 처음부터 가정 | 모든 data path에 user_id 필터, RBAC + rate limit + tenant isolation |
| Auth | Bearer token + admin/user RBAC | tokenrouter의 3단 hierarchy보다 단순, solo/multi-user 모두 적합 |

### 1.3 Non-goals (재확인)

- 자체 학습 ML 모델 (P2/P3)
- LLM-based triage (P1, optional)
- Admin UI / dashboard (P2)
- Quota billing tracking (P1)
- Output token shaping (P2)
- Image compression (P2)

## 2. Component overview

```
                    ┌──────────────────────────────────────────────┐
                    │         Token Saver Router (FastAPI)         │
                    │                                              │
  Client ───────────►  ┌──────────────────────────────────────┐    │
  (Claude Code,       │  HTTP Proxy (OpenAI-compatible)       │    │
   Codex, OpenCode,   │   /v1/chat/completions                │    │
   any OpenAI SDK)    │   /v1/models                          │    │
       │              │   /v1/ccr/retrieve (CCR-lite)        │    │
       │              │   /admin/health                       │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  Auth Middleware                     │    │
       │              │   - Bearer token → Redis session     │    │
       │              │   - RBAC check (admin/user)          │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  Rate Limit (Redis sliding window)   │    │
       │              │   per-user, per-minute               │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  Content Type Detector               │    │
       │              │   text / json / code / log           │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  Compressor Registry                 │    │
       │              │   - JSON compressor                  │    │
       │              │   - text trim compressor             │    │
       │              │   - (CCR-lite for reversibility)     │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  Provider Router                     │    │
       │              │   provider/model prefix → client     │    │
       │              │   OpenAI / Anthropic / Ollama        │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  CCR-lite (Redis hot + Mongo cold)    │    │
       │              │   - read: Redis → Mongo fallback      │    │
       │              │   - write: Mongo (TTL index)         │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       │              ┌──────────────────────────────────────┐    │
       │              │  Conversation Log (Mongo)             │    │
       │              │   user_id, provider, model, tokens    │    │
       │              └──────────────────────────────────────┘    │
       │                          │                               │
       │                          ▼                               │
       └───────────────  Forward to provider (HTTPS) ─────────────┘
```

### 2.1 내부 모듈 (소스 트리 매핑 예정)

| 모듈 | 책임 | LOC 예산 |
|---|---|---|
| `proxy/` | FastAPI app + OpenAI-compatible endpoints | 400 |
| `auth/` | Bearer token middleware + RBAC check | 200 |
| `ratelimit/` | Redis sliding window | 150 |
| `detector/` | Content type classifier (text/json/code/log) | 300 |
| `compressor/` | Pluggable compressor registry + 1-2 impls | 400 |
| `provider/` | Provider client interface + OpenAI/Anthropic impls | 500 |
| `ccr/` | CCR-lite (Redis read-through + Mongo store) | 350 |
| `cli/` | `token-saver serve` / `config` / `ccr` commands | 200 |
| `tests/` | Fixture-based regression | 300 |
| 합계 | | ~2,800 |

token-router (777 LOC 단일 파일) 와 headroom (32k+ LOC) 사이의 적정선. TASK-002에서 검증.

## 3. Request lifecycle

들어온 OpenAI-compatible 요청이 거치는 단계:

```
1. Auth (Bearer token 검증)
   └─ Redis GET session:{token} → user_id, role, expires_at
   └─ role ∈ {admin, user} check
   └─ 없거나 만료 → 401 Unauthorized

2. Rate limit
   └─ Redis INCR ratelimit:{user_id}:{minute_bucket} EX 60
   └─ > limit → 429 Too Many Requests

3. Request validation
   └─ model, messages 필수 필드 확인
   └─ provider/model prefix 파싱 → provider 결정
   └─ default_provider fallback (model prefix 없을 때)

4. Content type detection
   └─ messages 전체 content 를 보고 mode 결정
   └─ heuristic: JSON regex / code syntax marker / log pattern / 그 외 text
   └─ mode → compressor 매핑

5. CCR-lite lookup (lossless retrieval 우선)
   └─ content_hash = sha256(messages_normalized)
   └─ Redis GET ccr:{content_hash} (TTL 300s)
   └─ hit → cached_response 반환, Mongo hits++ (비동기)
   └─ miss → Mongo ccr_store.find({content_hash, user_id, expires_at > now})
   └─ hit → Redis SETEX + 반환
   └─ miss → 다음 단계

6. Compression (lossy mode 일 때만)
   └─ mode별 default compressor 적용
   └─ compressor: messages → messages' (tokens 감소)
   └─ CCR storage key 생성 (reversibility)

7. KV cache alignment hint
   └─ prefix hash 계산 (system + tools + 첫 N messages)
   └─ Redis GET kv_cache:{provider}:{prefix_hash}
   └─ hit → provider cache_control block 에 hint 첨부
   └─ miss → prefix stabilization (날짜 정규화 등)

8. Provider client 호출
   └─ Provider client Protocol.invoke(messages', options)
   └─ SSE streaming passthrough
   └─ Timeout / retry policy

9. CCR-lite store (lossy mode 일 때)
   └─ Mongo ccr_store.insert({content_hash, user_id, compressed, original_meta, content_type, expires_at})
   └─ TTL index 활용 (expires_at < now 자동 삭제)

10. Conversation log
    └─ Mongo conversations.insert({user_id, provider, model, input_tokens, output_tokens, latency_ms, ts})
    └─ 비동기 batch insert 가능 (P1)

11. Response 반환
    └─ OpenAI-compatible JSON 형식
    └─ usage 필드 (prompt_tokens, completion_tokens, total_tokens) 포함
```

### 3.1 Streaming 처리

SSE 응답의 경우:
- 8번 단계에서 stream iterator 를 그대로 client 에 passthrough
- 9-10번 단계는 background task 로 분리
- usage field 는 stream 끝에서 한 번에 계산

## 4. Data layer

### 4.1 Redis (hot path, TTL 적합)

| Key pattern | Type | TTL | Purpose |
|---|---|---|---|
| `session:{token}` | hash | 1h | Bearer token → user info (id, role, expires_at) |
| `ratelimit:{user_id}:{minute_bucket}` | int | 60s | Per-user per-minute counter |
| `kv_cache:{provider}:{prefix_hash}` | string | 5min | Provider cache_control hint |
| `ccr:{content_hash}` | hash | 5min | CCR-lite read-through cache |
| `semantic:{query_hash}` | hash | 5min | Semantic cache recent responses |
| `provider_health:{provider}` | string | 30s | Provider circuit breaker state |

### 4.2 Mongo (cold path, 영구+queryable)

#### Collections

**users**
```json
{
  "_id": "user_{ulid}",
  "email": "user@example.com",
  "role": "admin|user",
  "api_key_hash": "argon2id-hash",
  "created_at": ISODate,
  "updated_at": ISODate,
  "last_active_at": ISODate,
  "metadata": { ... }
}
```

**providers** (per-user config)
```json
{
  "_id": "provider_{ulid}",
  "user_id": "user_{ulid}",
  "name": "openai-main",
  "type": "openai|anthropic|ollama",
  "api_key_encrypted": "AES-GCM(encrypted-by-master-key)",
  "config": { "base_url": "...", "default_model": "..." },
  "enabled": true,
  "created_at": ISODate,
  "updated_at": ISODate
}
```

**ccr_store**
```json
{
  "_id": "sha256:content_hash",
  "user_id": "user_{ulid}",
  "content_type": "text|json|code|log",
  "compressed": "...",          // 압축된 content
  "original_meta": { ... },     // 원본 위치/크기 등 (raw byte 는 안 저장 — disk cache optional P1)
  "compressor": "text-trim-v1",
  "tokens_before": 1234,
  "tokens_after": 567,
  "hits": 0,
  "created_at": ISODate,
  "expires_at": ISODate
}
```
Indexes:
- `{ user_id: 1, content_type: 1, expires_at: 1 }`
- `{ expires_at: 1 }` (TTL index — Mongo auto-delete)

**conversations**
```json
{
  "_id": "conv_{ulid}",
  "user_id": "user_{ulid}",
  "provider": "openai",
  "model": "gpt-4",
  "request_id": "req_{ulid}",
  "input_tokens": 1234,
  "output_tokens": 567,
  "latency_ms": 1234,
  "compression_applied": true,
  "ccr_hit": false,
  "ts": ISODate
}
```
Indexes:
- `{ user_id: 1, ts: -1 }`
- `{ ts: -1 }` (TTL index — 90일 retention default)

**audit_log**
```json
{
  "_id": "audit_{ulid}",
  "user_id": "user_{ulid}",
  "action": "auth.login|provider.add|ccr.retrieve|...",
  "resource": "...",
  "result": "success|failure",
  "ip": "1.2.3.4",
  "ts": ISODate
}
```
Indexes:
- `{ user_id: 1, ts: -1 }`
- `{ action: 1, ts: -1 }`

### 4.3 Multi-user isolation 원칙

**모든 data path 에 `user_id` 필터 필수**:

1. **Redis**: key prefix `user:{user_id}:...` (혹은 별도 keyspace + filter)
2. **Mongo**: 모든 query 에 `{ user_id: <current_user_id> }` 조건 필수. query builder helper 로 강제.
3. **Cross-user access 차단**: admin role 만 다른 user 의 data 조회 가능. audit log 남김.
4. **Provider config 격리**: user A 의 API key 로 user B 가 호출 불가. Redis session 에 user_id 박혀있고 provider lookup 시 user_id 검증.

### 4.4 Encryption

- **API key (provider)**: master key (env var `TOKEN_SAVER_MASTER_KEY`) + AES-GCM. master key 분실 시 복구 불가.
- **Bearer token**: argon2id-hash (저장). token 자체는 stateless.
- **Mongo connection**: TLS 권장 (production). docker-compose 는 local network 가정.
- **Redis connection**: TLS 권장 (production). docker-compose 는 local network 가정.

## 5. Auth + RBAC

### 5.1 Auth flow

```
Client → POST /v1/auth/login { email, password }
  └─ verify password (argon2id against users.api_key_hash)
  └─ generate Bearer token (random 256-bit)
  └─ Redis SET session:{token} {user_id, role, expires_at} EX 3600
  └─ return { token, expires_in: 3600 }

Client → GET /v1/...
  Header: Authorization: Bearer {token}
  └─ Auth middleware
     └─ Redis GET session:{token}
     └─ if exists: req.state.user = {id, role}; next()
     └─ if not exists: 401 Unauthorized
```

### 5.2 RBAC matrix

| Endpoint | user | admin |
|---|---|---|
| `POST /v1/auth/login` | ✓ | ✓ |
| `POST /v1/chat/completions` | ✓ | ✓ |
| `GET /v1/models` | ✓ | ✓ |
| `POST /v1/ccr/retrieve` | ✓ | ✓ |
| `GET /v1/conversations` | own only | all |
| `POST /v1/providers` | ✓ | ✓ |
| `GET /v1/users` | ✗ | ✓ |
| `GET /admin/health` | ✗ | ✓ |
| `GET /admin/audit` | ✗ | ✓ |

## 6. Deployment

### 6.1 Local development (docker-compose)

```yaml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    volumes: ["redis-data:/data"]
  mongo:
    image: mongo:7
    ports: ["27017:27017"]
    volumes: ["mongo-data:/data/db"]
    environment:
      MONGO_INITDB_ROOT_USERNAME: token_saver
      MONGO_INITDB_ROOT_PASSWORD_FILE: /run/secrets/mongo_password
  token-saver:
    build: .
    ports: ["8787:8787"]
    environment:
      REDIS_URL: redis://redis:6379
      MONGO_URL: mongodb://token_saver:${MONGO_PASSWORD}@mongo:27017
      TOKEN_SAVER_MASTER_KEY: ${MASTER_KEY}
    depends_on: [redis, mongo]
volumes:
  redis-data:
  mongo-data:
```

### 6.2 Production (P1)

- TLS termination: ALB / nginx reverse proxy
- Redis: managed Redis (ElastiCache / Memorystore)
- Mongo: Atlas / DocumentDB
- Secret management: env vars via KMS / Vault
- Monitoring: Prometheus exporter (P1)

## 7. Cross-reference

| Architecture component | Concept reference | External reference |
|---|---|---|
| HTTP proxy OpenAI-compat | §1.2, §4.1 | tokenrouter `pkg/proxy/server.go` |
| Auth + RBAC | §6 Q2 | tokenrouter `cmd/toro/main.go` (key hierarchy 차용) |
| Compressor registry | §4.1 | headroom `crates/headroom-core/src/transforms/pipeline/` |
| CCR-lite concept | §4.1 | headroom `crates/headroom-core/src/ccr/` (단순화) |
| 3-mode taxonomy | §4.1 | token-router `scripts/router.py` (Ollama 의존 제거) |
| Fixture regression | §4.1 | token-router `tests/router-tests.json` |
| KV cache alignment | §3 step 7 | headroom `headroom/cache/{anthropic,openai}.py` |

## 다음에 읽을 문서

- [Concept](./token-saver-concept.md) — 3 reference 비교 + cherry-pick 매트릭스 + 결정 lock-in
- [Session Handoff](../../ai-workflow/memory/active/session_handoff.md) — TASK-002 시작 컨텍스트
- [Work Backlog](../../ai-workflow/memory/active/work_backlog.md) — TASK-002 sub-task breakdown