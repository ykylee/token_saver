<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: stable (TASK-002-5-a done, TASK-002-5-b done, TASK-002-6 ready)
- Updated: 2026-06-27
- Related docs: [Project Profile](../../docs/PROJECT_PROFILE.md), [Concept](../../docs/concepts/token-saver-concept.md), [Architecture](../../docs/architecture.md), [Work Backlog](./work_backlog.md)

## Current Focus

- TASK-001 done — concept + architecture 영구 보존 완료, Q1/Q2 lock-in 완료
- TASK-002-1 done — project skeleton
- TASK-002-2 done — OpenAI-compatible endpoints (mock)
- TASK-002-3 done — Bearer token auth + session store + RBAC + admin-only `/admin/health` gate
- TASK-002-4 done — Mongo users collection + admin seed + indexes + factory (memory/mongo backend switch)
- TASK-002-5-a done — Provider core (BaseProvider Protocol + OpenAI-compat + Anthropic impls + Registry + Router + chat forward via httpx) + respx fixture
- TASK-002-5-b done — ProviderStore (in-memory + Mongo) + CRUD routes (test/add/list/refresh/delete) + RBAC (own-only / all) + Redis models cache + RedisSessionStore + AES-GCM crypto (api_key + base_url encryption) + per-user chat_completions lookup
- 다음 세션 시작: TASK-002-6 (Fixture-based regression test + SSE streaming passthrough)

## Work Status

- TASK-001 라우터 아키텍처 및 scope 정의: **done** (commits `e921182` `91fef89` `6bbb7c9`)
- TASK-002-1 project skeleton: **done** (commit `d50fb97`)
- TASK-002-2 OpenAI-compatible endpoints (mock): **done** (commit `cba86bc`)
- TASK-002-3 Bearer token auth + RBAC: **done** (commit `0f97b4c`)
- TASK-002-4 Mongo users collection + admin seed + indexes: **done** (commit `2f069d7`)
- TASK-002-5-a Provider core + chat real forward: **done** (commit `b59f0e4`)
- TASK-002-5-b Provider store + CRUD + Redis cache + Redis session + AES-GCM crypto: **done** (commit `7884dbd`)
- TASK-002 MVP 1차 cycle: **in_progress** (TASK-002-6, TASK-002-7 remaining)

## Key Changes (2026-06-27, TASK-002-5-b 누적)

### AES-GCM crypto (`auth/crypto.py` 보강)
- `encrypt_secret(plain, *, master_key)` → `base64(nonce || ciphertext || tag)` (12-byte random nonce, AES-256-GCM)
- `decrypt_secret(token, *, master_key)` → 원본 (tag mismatch 시 `InvalidCiphertextError`)
- `derive_master_key(b64_key)` → 32-byte 검증, 잘못된 길이/base64 시 `InvalidMasterKeyError`
- Master key 분실 = 영구 손실 (architecture §4.4 의도된 failure mode)
- `Encrypt_on_insert / decrypt_on_read` 패턴: store 가 암호화 책임지고, caller 는 plaintext 만 봄

### ProviderStore Protocol (`provider/store.py` 신규)
- Protocol methods: `list_for_user(user_id)`, `get(id, user_id)`, `create(...)`, `update(...)`, `delete(id, user_id)`, `set_models_cache(id, user_id, models=...)`, `list_enabled_for_user(user_id)`
- Multi-tenant isolation at the store boundary: `user_id=None` = admin's cross-tenant view, `user_id=<id>` = tenant filter
- `InMemoryProviderStore` (테스트 + memory backend) — no encryption
- `MongoProviderStore` (mongomock-friendly CRUD + `ensure_indexes()` + ciphertext-at-rest + 32-byte master_key 검증) — `close()` lifecycle
- `_doc_to_record(doc, master_key)` helper — encrypted blob → `ProviderRecord` (api_key, base_url decrypted)

### Redis models cache (`provider/cache.py` 신규)
- Protocol `ModelCatalogCache` + `RedisModelCatalogCache` (production) + `NullModelCatalogCache` (test fallback)
- Key shape: `provider_models:{provider_id}` (architecture §4.1 정합)
- TTL = `Settings.provider_models_cache_ttl_seconds` (default 3600s = 1h)
- Best-effort: 모든 에러 swallow → cache miss / no-op. Hot path 가 Redis 장애로 5xx 안 됨
- JSON-encoded `[{"id","owned_by"}, ...]` payload

### RedisSessionStore (`auth/tokens.py` 보강)
- Key shape: `session:{token}` (architecture §4.1 정합)
- JSON-encoded `SessionPayload(user_id, role, expires_at)` + `EX`-based TTL (Redis 자체 eviction)
- `auth/factory.build_session_store(settings, redis_client)` — Redis 가 있으면 Redis, 없으면 InMemory

### Provider factory (`provider/factory.py` 신규)
- `build_provider_store(settings, *, master_key)` → `(InMemory | Mongo)ProviderStore, optional motor client`
- `build_redis_client(settings)` → `Redis | None` (`Settings.redis_url` empty 면 None)
- `build_model_cache(settings, redis_client)` → `RedisModelCatalogCache | NullModelCatalogCache`
- Single decision point (mirrors `auth.factory.build_user_store`)

### CRUD routes (`proxy/routes/providers.py` 신규)
- `POST /v1/providers/test` — auth required, no persistence, `ProviderTestResult` (ok + latency + sample_models + error) 반환
- `POST /v1/providers` — auth required, owner = current user, connection probe (race-condition guard) → 502 on test failure, 201 with `ProviderInfo` (api_key 미포함) on success
- `GET /v1/providers` — auth required, admin sees all, regular sees own (`_scope_user_id` helper)
- `POST /v1/providers/{id}/models/refresh` — auth required, refreshes catalog, writes to Redis cache + Mongo `models_cache`. Returns `ModelsRefreshResult(provider_id, models_count, added, removed)`
- `DELETE /v1/providers/{id}` — auth required, admin can delete any, regular only own. Invalidates Redis cache too
- `_record_to_info(record)` — `ProviderRecord` (api_key 포함) → `ProviderInfo` (api_key 미포함) projection (wire leak 방지)
- `_http_502(provider_error)` helper — connection / response / config error 통합 502

### chat_completions per-user lookup (`proxy/routes/chat_completions.py` 갱신)
- `_build_registry(records, registry)` helper — `ProviderRecord` list → `ProviderRegistry` (skip unknown types)
- 매 request 마다 `provider_store.list_enabled_for_user(user.id)` 호출 → transient registry + `ProviderRouter`
- Auth required (기존 test fixture 에 `admin_auth_header` 추가)
- `finally: await registry.aclose()` — per-request httpx client leak 방지

### /v1/models 갱신 (`proxy/routes/models.py` 갱신)
- Auth required
- Returns one `ModelCard` per *enabled* provider's `default_model` (operator-chosen representative, not full catalog)

### lifespan wiring (`proxy/app.py` 갱신)
- `derive_master_key(resolved.master_key)` → 32 bytes (InvalidMasterKeyError at startup)
- Build sequence: `build_user_store` → `build_provider_store(master_key=...)` → `build_redis_client` → `build_session_store` → `build_model_cache`
- Startup: `MongoProviderStore.ensure_indexes()` + 기존 `MongoUserStore.ensure_indexes()` + `seed_admin`
- Shutdown: Redis client `aclose()` → mongo `close()` → `provider_registry.aclose()`

### Settings (`config.py` 보강)
- `provider_store_backend: Literal["memory", "mongo"] = "memory"` 신규

### Tests (79 신규)
- `test_crypto.py` (8): roundtrip, nonce uniqueness, tamper fail, truncated fail, non-base64 fail, master_key length/base64 검증, encrypt 거부
- `test_in_memory_provider_store.py` (15): CRUD + multi-tenant filter + owner enforcement on get/update/delete + set_models_cache + list_enabled_for_user + parametrized empty store
- `test_mongo_provider_store.py` (12): cipher-at-rest verification + roundtrip + multi-tenant filter + owner enforcement + ensure_indexes idempotent + wrong-key decrypt fail + constructor validation
- `test_session_store.py` (8): Redis roundtrip + TTL set + revoke + missing-token + aclose + InMemorySessionStore lazy-evict regression
- `test_model_cache.py` (9): Redis roundtrip + miss + invalidate + TTL + corrupt entry + null cache always-miss + null set/invalidate noop + null aclose noop
- `test_provider_store_factory.py` (5): memory/mongo branch + unknown backend + mongo short-key validation
- `test_provider_routes.py` (15): test endpoint (200 ok / 200 with ok=false / 401) + create (201 / 502 / 401) + list (empty / own / RBAC) + refresh (200 / 404) + delete (204 / 404 / RBAC) + _scope_user_id helper
- 기존 `test_chat_completions.py` + `test_chat_completions_forwarding.py` + `test_v1_models.py` 갱신 (per-user auth + CRUD round-trip)

## Next Actions

- [ ] TASK-002-6: Fixture-based regression test (full end-to-end via Provider CRUD + chat forward) + SSE streaming passthrough (stream=true)
- [ ] TASK-002-7: CLI `token-saver serve` + `provider test` + `provider add` + `provider list` + `provider refresh` + `provider delete`
- [ ] TASK-002 done → first release v0.1.0 (PEP 440 `0.1.0a1`? 검토 필요)

## Risks & Blockers

- **Q1/Q2 결정**: resolved (Python-only 1차, Redis+Mongo multi-user)
- **MVP scope 적정성**: cumulative 7198 LOC = 240% of budget (3000) — TASK-002-5-b 가 architecture §4.4 encryption + multi-tenant + redis cache + redis session + CRUD 를 다 들여서 예산보다 큰폭 초과. Retrospective cleanup (4 phase) 대상 (TASK-002-7 후).
- **PEP 440 표기 fix**: pyproject `0.1.0-alpha` → `0.1.0a0` (wheel normalize 시 `-` 이 사라져서 version drift risk). 이후 release 도 PEP 440 strict.
- **TestClient deprecation warning**: starlette 1.x 의 httpx 사용 deprecation. FastAPI 0.138 / starlette 1.3.x 영향. 1차 release 전 httpx2 마이그레이션 검토.
- **OpenAI/Anthropic API rate limit**: proxy가 단일 client보다 2-3x traffic 발생 가능 → burst protection P1
- **Bearer token rotation**: 1차 release 는 TTL 1h 단일. rotation API 는 P1.
- **Master key rotation**: 현재 store 생성 시 1회 주입. rotation 은 app restart 필요 (P1 follow-up: re-key helper)
- **Mongo ProviderStore.client 공유**: user store + provider store 가 별도 `AsyncIOMotorClient` 생성 (same URL 일지라도). Connection pool 2개. 후속 cycle 에서 client 공유 검토.

## Reference for next session

- **핵심 결정 (TASK-001 lock-in + follow-up)**:
  - Q1: Python-only 1차 (FastAPI + uvicorn). Rust core는 P2.
  - Q2: Redis (hot) + Mongo (cold) 조합. multi-user 처음부터 가정.
  - Local LLM: 별도 서버 + URL/port/API key + Models API 동적 discovery
- **Component map**: `docs/architecture.md` §2 (Provider Registry + Provider Store 추가)
- **Request lifecycle (12+ steps)**: `docs/architecture.md` §3
- **Provider registration flow**: `docs/architecture.md` §4.2 (POST /v1/providers/test + /providers + /models/refresh + DELETE)
- **Redis schema**: `docs/architecture.md` §4.1 (7 key patterns + TTL) — `session:{token}` + `provider_models:{provider_id}` 구현됨
- **Mongo schema**: `docs/architecture.md` §4.2 (5 collections + indexes) — `providers` collection 신규 (encrypted base_url/api_key)
- **Encryption**: `docs/architecture.md` §4.4 (AES-GCM master key + encrypted base_url + api_key)
- **RBAC matrix**: `docs/architecture.md` §5.2 (13 endpoints) — providers CRUD 추가 (own-only / all)
- **docker-compose**: `docs/architecture.md` §6.1 (local LLM 제거, 별도 서버 가동)
- **LOC 예산 ~3,000**: `docs/architecture.md` §2.1 — **초과 (240%)**; 후속 cycle 에서 cleanup
- **TASK-002-1 skeleton LOC**: 507 (src 251 + tests 256) — ~17% of budget
- **TASK-002-2 cumulative LOC**: 1240 (src ~500 + tests ~740) — ~41% of budget
- **TASK-002-3 cumulative LOC**: 2098 (src ~870 + tests ~1230) — ~70% of budget
- **TASK-002-4 cumulative LOC**: 2701 (src ~1180 + tests ~1520) — ~90% of budget
- **TASK-002-5-a cumulative LOC**: 3987 (src ~1620 + tests ~2370) — **133% of budget** (over)
- **TASK-002-5-b cumulative LOC**: 7198 (src ~3300 + tests ~3900) — **240% of budget** (well over)
- **Build verification (TASK-002-1..5-b)**: ruff clean, mypy clean (34 source files), pytest 161 passed

## 환경 노트

- 작업 디렉토리: `/Users/yklee/repos/token_saver`
- venv: `.venv/` (Python 3.14.5, editable install, dev extras)
- New dev dep: `fakeredis>=2.20` (in-process Redis for session + cache tests)
- MCP transport_ready: false — TASK-002-6 와 함께 enable 검토
- git remote: `origin` → `https://github.com/ykylee/token_saver.git` (tracking main)
- TestClient deprecation warning: starlette 1.x 의 httpx → httpx2 마이그레이션 검토 필요