<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# Token Saver Router — Concept & Reference Analysis

- 문서 목적: token_saver의 컨셉 정의, 외부 reference 3종 비교 분석, 그리고 cherry-pick으로 도출한 MVP scope를 영구 보존한다.
- 범위: 프로젝트 목적, reference 1차/2차 출처 비교, layer-by-layer 장단점, MVP scope lock-in
- 대상 독자: 토큰 절감 라우터를 처음 보는 개발자, AI 에이전트, 후속 세션
- 상태: draft (TASK-001 진행 중, lock-in 후 stable 전환)
- 최종 수정일: 2026-06-24
- 관련 문서: [Project Profile](../PROJECT_PROFILE.md), [Workflow Index](../../ai-workflow/WORKFLOW_INDEX.md), [Session Handoff](../../ai-workflow/memory/active/session_handoff.md)

## 1. 컨셉 정의

### 1.1 문제 정의

AI 에이전트(Claude Code, Codex, OpenCode, Gemini CLI 등)가 클라우드 LLM을 호출할 때 **prompt token + tool output token + cached prefix** 비용이 누적된다. 1) tool output이 거대하거나, 2) 같은 context가 반복되거나, 3) provider가 비싸면 비용이 폭증한다.

핵심 가정:
- 토큰 비용은 **2가지 layer**에서 발생: (a) **input context** (tool output + message history), (b) **provider rate × 모델 단가**.
- 절감은 **3가지 lever**로 가능: (i) **content-type 인지 처리** (어떤 content가 들어왔는지에 따라 압축 vs lossless vs raw), (ii) **cache alignment** (provider KV cache hit 극대화), (iii) **provider routing** (같은 task를 더 싼/적합한 모델로).
- 단일 lever만으로는 효과가 제한적. **policy engine**이 content type + context + quota를 보고 위 3 lever를 동시에 결정해야 함.

### 1.2 해결 컨셉

> "OpenAI-compatible proxy에서 들어온 요청을 **content type과 policy**로 보고, 어떻게 처리할지 결정하는 **policy engine**. mode별로 lossless(line routing) 또는 lossy(compression)를 선택하고, 결과를 provider에 전달."

핵심 결정 3개:
1. **Entry mode**: OpenAI-compatible HTTP proxy (tokenrouter 차용). adoption friction 0 — `OPENAI_BASE_URL` 한 줄로 모든 client가 자동 연결.
2. **Processing mode**: 3-mode taxonomy (text / json / code) + log/code sub-mode. token-router 차용.
3. **Reversibility**: lossy 압축은 CCR-lite (hash → SQLite store + retrieval)로 보완. headroom 차용.

### 1.3 비-목표 (Non-goals)

- 자체 학습된 ML compressor 모델 (Kompress류): cold-start 비용 + CDN 의존성 회피.
- `learn` 자동 실패 마이닝: 별도 프로젝트 가능, 본 프로젝트 out-of-scope.
- Admin UI / dashboard: solo/local-first 사용자에 과함. JSON log + CLI로 충분.
- Quota tracking / billing: 1차 사용자에 과함.
- Output token shaping (verbosity steering): counterfactual 측정 한계.

## 2. Reference 프로젝트 비교

### 2.1 1차 출처

| 프로젝트 | repo | stars | LOC | 언어 |
|---|---|---|---|---|
| **headroom** | https://github.com/headroomlabs-ai/headroom | 49.3k | ~32k (Rust core) + Python | Python + Rust |
| **tokenrouter** | https://github.com/lkarlslund/tokenrouter | 15 | ~25k | Go |
| **token-router** | https://github.com/sleeplesshan/token-router | 72 | ~921 (router.py 777 + test 140) | Python |

모든 repo는 `~/repos/harness-refs/` 에 `--depth=1` 으로 클론되어 있음 (yklee 관리).

### 2.2 각 프로젝트 핵심 접근

**headroom**: "Compress everything before LLM"
- Pipeline: `CacheAligner → ContentRouter → CCR`
- 6 compressor 알고리즘 (SmartCrusher, CodeCompressor, Kompress-base, CacheAligner, CCR, Image)
- 3 entry mode (library, proxy, MCP)
- `headroom learn`: 실패 session 마이닝 → AGENTS.md 자동 보강
- KV cache alignment: Anthropic `cache_control` block, OpenAI prefix stabilization, Google CachedContent

**tokenrouter**: "One OpenAI-compatible endpoint for all providers"
- OpenAI-compatible `/v1` HTTP endpoint
- `provider/model` prefix 라우팅
- Token hierarchy: `admin` / `keymaster` / `inferrer`
- 임시 subordinate key (TTL 기반)
- Admin dashboard (HTML/JS) + usage analytics
- OAuth refresh, TLS modes

**token-router**: "Lossless local line routing"
- Local Ollama (Gemma 4 2B) 가 line coordinate 반환
- Python script 가 원본 file을 raw byte slice
- 3 mode: `error_log` / `heavy_code` / `agent_context`
- Deterministic prefilter → LLM disambiguation 2-stage
- Output caps + memory safety (`OLLAMA_KEEP_ALIVE=0s`, `NUM_CTX=8192`)
- Fixture-based regression test

### 2.3 장단점 분석

#### 2.3.1 headroom

**장점**:
- 검증된 production-grade: 49.3k★, v0.27.0, 1,715 commits, 4 provider 실전 검증.
- Modular transform pipeline: `transforms/{content_detector, smart_crusher, log_compressor, search_compressor, diff_compressor, tag_protector, anchor_selector, adaptive_sizer, live_zone, safety}` 10개 독립 transform을 `pipeline/pipeline.rs`에서 조합. separation of concerns 깨끗.
- CacheControl 추상화: `CacheStrategy` enum (`PREFIX_STABILIZATION` / `EXPLICIT_BREAKPOINTS` / `CACHED_CONTENT` / `NONE`) — provider별 다른 캐싱 전략을 추상화. **그대로 차용 가능**.
- CCR (Compressed Context Retrieval): LLM이 압축된 결과만 보고, 원본 필요 시 `headroom_retrieve`로 retrieval. 토큰 절감과 정확성 보존의 tradeoff 해결. **컨셉은 강력**.
- 3 entry mode (library/proxy/MCP): adoption friction 최소.
- `headroom learn`: 실패 session 마이닝해서 AGENTS.md에 자동 보강. **컨셉 독보적**.

**단점**:
- 코드 규모 = 유지보수 부담: Rust core 32,314 LOC, smart_crusher 하나만 7,000 LOC.
- ML 모델 의존성: Kompress-v2-base (HF) 다운로드 필수 → CDN/pyke.io 의존. corporate network / air-gapped에서 동작 어려움.
- 변환 정확도 ↔ latency tradeoff: `dynamic_detection_tiers: regex / ner(spaCy) / semantic` — semantic tier 켜면 20-50ms 추가.
- Output shaping counterfactual 측정 한계: 정확히 얼마나 절약됐는지 알 수 없음 (`estimated`로만 표기).
- Tag protection (PII) `tag_protector.rs` 1,295 LOC — regex 기반이라 recall 낮을 수밖에 없음.
- Provider coupling: Anthropic / OpenAI / Google 3개 캐싱 API 모두 가정. 새 provider 추가 시 큰 작업.
- Distribution 복잡: maturin build + Rust toolchain + ONNX runtime + HF model download — 첫 install이 무거움.

#### 2.3.2 tokenrouter

**장점**:
- 단일 binary 배포: Go 빌드 → 단일 `torod` 바이너리. venv / toolchain 필요 없음.
- OpenAI-compatible 1차 endpoint: `OPENAI_BASE_URL=http://127.0.0.1:7050/v1` 한 줄로 모든 client 자동 연결. **adoption friction 0**.
- Multi-provider aggregation: `provider/model` prefix 또는 default_provider fallback.
- RBAC + 임시 키: `admin/keymaster/inferrer` hierarchy + `toro --ttl 8h`로 휘발성 subordinate key 발급. "장기 키를 day-to-day 툴에 박지 않는다"는 보안 모델. **단순하고 강력**.
- Usage analytics 내장: latency, TPS, per-provider/model/key/IP breakdown.
- Rolling release: goreleaser 기반 deb/rpm/archlinux 패키지.
- OAuth refresh: Copilot CLI 토큰 자동 갱신 같은 운영 디테일.

**단점**:
- Token compression 0: 라우팅 + 운영에만 집중. **우리 목적의 핵심 영역 부재**.
- 코드 규모 대비 효율 낮음: 25k LOC Go 중 admin UI / HTML / JS 가 20%+. 진짜 가치는 backend logic 인데 그것만 보면 절반도 안 됨.
- DB 의존: `pkg/usagedb/store.go` 960 LOC + `pkg/conversations/store.go` 534 LOC + `pkg/logstore/store.go` 519 LOC → SQLite 기반. migration 관리 필요.
- Provider list vendor-specific: `pkg/provider/client.go` 337 LOC — provider 4-5개를 inline 구현. 새 provider마다 직접 작성.
- Admin UI 가 본질: HTTP `/admin` 페이지가 heavy — solo/local-first 사용자에겐 과함.
- Test mock-heavy: 30+ test file. 표면적 넓고 brittle.
- conversations full-text 저장: storage growth. privacy 부담.

#### 2.3.3 token-router

**장점**:
- 극단적 simplicity: 777 LOC 단일 파일. **읽고 검증하기 가장 쉬움**. 전체 로직 한 화면.
- Lossless (원본 보존): 압축 아니라 line coordinate만 받아서 raw byte slice. **정확도 손실 0**. headroom과 결정적으로 다른 디자인 철학.
- 3 mode 명확한 taxonomy: `error_log` / `heavy_code` / `agent_context` — content type별 다른 prefilter + 다른 context_lines.
- Deterministic prefilter → LLM triage 2-stage: regex keyword → tail window → query hit → structural marker → 마지막 Ollama disambiguate. **빠르고 결정적인 경로 먼저, LLM은 마지막 disambiguation만**.
- Local model triage: Gemma 4 2B Q4_K_M. **triage 비용 0에 가까움** (이미 local). API 호출 없음.
- Output caps + memory safety: `ROUTER_MAX_OUTPUT_LINES=160` 강제 + `OLLAMA_KEEP_ALIVE=0s` 즉시 unload + `OLLAMA_NUM_CTX=8192` 상한. **resource leak 방어가 design에 박힘**.
- Fixture-based regression: `tests/router-tests.json` + `scripts/run_router_tests.py` — mode별 expected output. **테스트가 spec과 1:1 매핑**.
- Query pass-through: `--query "token expiration"` 이 local model에 그대로 노출 → multi-word tokenize 후 부분 매치.
- Agent integration: `agents/openai.yaml` + Codex skill 디렉토리 카피 — 단순 명료.

**단점**:
- Standalone CLI only: HTTP endpoint 없음. 매번 subprocess로 호출 → CI/agent loop에서 비효율.
- Static routing, dynamic 학습 없음: 같은 파일 + 같은 query → 같은 결과. cache 개념 없음.
- Local model 의존: Ollama 설치 필수. **adoption barrier**.
- Streaming 미고려: `ROUTER_STREAM_THRESHOLD_BYTES`는 streaming prefilter일 뿐, 응답 streaming은 없음.
- JSON failure mode 약함: "Very small local models may occasionally return invalid JSON" 명시적 한계. fallback은 있지만 quality 낮음.
- Provider routing 0: 어느 LLM으로 보낼지 결정 안 함. **triage만, 최종 cloud call은 user 몫**.
- Extension 어려움: 단일 파일 → 새 mode 추가하면 함수 + regex + fixture 추가. dispatch 구조 없음.

## 3. Cherry-pick 매트릭스

| Layer | headroom | tokenrouter | token-router | 채택 / 비고 |
|---|---|---|---|---|
| **Entry mode** | proxy+lib+MCP | proxy only | CLI only | **proxy + lib** (headroom 패턴, MCP는 P2) |
| **Provider routing** | 약함 | **강함** (`provider/model` prefix) | 없음 | **채택: tokenrouter prefix 방식** |
| **Provider client 추상화** | provider-specific inline | provider-specific inline | 없음 | **채택: tokenrouter client interface**, 구현은 1-2개부터 |
| **Token compression** | **강함** (6 algorithm) | 없음 | 없음 | **채택: ContentRouter + pluggable compressor registry 컨셉**, 알고리즘 1개(text trim)부터 |
| **CCR (reversible)** | **독보적** | 없음 | 있음 (line은 원본) | **채택: CCR-lite** — hash → SQLite + retrieve |
| **KV cache alignment** | **독보적** (3 strategy) | 없음 | 없음 | **채택: CacheControl abstraction**, PREFIX_STABILIZATION 우선 구현 |
| **Triage (LLM-based)** | 부분 (Kompress) | 없음 | **독보적** (Ollama local) | **채택: token-router 3-mode + deterministic prefilter**, LLM triage는 옵션 (optional) |
| **Lossless vs Lossy** | lossless + lossy 둘 다 | N/A | **lossless** | **둘 다 제공**: lossless mode (line routing) + lossy mode (compression) |
| **Output token shaping** | 있음 (verbosity steering) | 없음 | 없음 | **skip (P2)** — counterfactual 측정 한계 |
| **`learn` (실패 마이닝)** | **독보적** | 없음 | 없음 | **skip (P2)** — high effort, separate project 가능 |
| **RBAC** | 없음 | **강함** | 없음 | **단순 채택: admin / user 2단**, full hierarchy 과함 |
| **Quota** | 없음 | **강함** | 없음 | **skip (P1)** — single user local-first 에 과함 |
| **Admin UI** | dashboard 있음 | **강함** | 없음 | **skip** — JSON log + CLI 부터 |
| **DB** | SQLite (CCR store) | SQLite (usage/conversations) | 없음 | **SQLite 단일** (CCR store + light usage log) |
| **Language** | Python+Rust | Go | Python | **Python (FastAPI)**, Rust core는 P2 |
| **Distribution** | maturin+wheel+npm | go install (single binary) | pip + Codex skill copy | **pip wheel + run script** |
| **Tests** | pytest + eval suite | Go testing + mock | **fixture-based regression** | **채택: token-router fixture 패턴**, JSON expected output 1:1 |

## 4. MVP Scope (P0)

### 4.1 Core components

1. **HTTP proxy (FastAPI)**
   - OpenAI-compatible `/v1/chat/completions`, `/v1/models`, `/v1/embeddings` 응답
   - Anthropic 호환 adapter (Messages API) — adapter pattern
   - Streaming response 지원 (SSE passthrough)

2. **Provider router**
   - tokenrouter식 `provider/model` prefix + default_provider fallback
   - 시작 provider: OpenAI, Anthropic (1-2개)
   - Provider client `Protocol` 정의

3. **Content type detector**
   - 3-mode taxonomy (text / json / code) + log sub-mode
   - token-router 차용, Ollama 의존 없이 deterministic heuristic만
   - mode별 다른 context_lines, max_output_lines

4. **Pluggable compressor registry**
   - `BaseCompressor` Protocol + `CompressorRegistry`
   - JSON compressor 1개 (smart dump 단순화) + text trim 1개로 시작
   - mode별로 기본 compressor 매핑

5. **CCR-lite**
   - Content hash (SHA256) → SQLite store
   - `retrieve` endpoint (`POST /v1/ccr/retrieve`)
   - TTL 적용 (default 1h, configurable)

6. **Output cap**
   - `MAX_OUTPUT_TOKENS` hard cap (per-request, per-session)
   - 초과 시 truncate + warning

7. **CLI + JSON config**
   - `token-saver serve` / `token-saver config` / `token-saver ccr list` 등
   - 설정은 JSON 또는 TOML 단일 파일

8. **Fixture-based regression**
   - token-router 패턴 그대로
   - `tests/fixtures/*.json` + `tests/run_tests.py`
   - 각 mode별 expected output 1:1

### 4.2 Non-goals (P0에서 명시적 제외)

- LLM-based triage (Ollama 의존 X)
- ML compressor 모델 (Kompress류)
- `learn` 자동 학습
- Admin UI / dashboard
- Quota / billing tracking
- OAuth refresh / token hierarchy
- Output token shaping
- Image compression
- AST compressor (Python 우선은 P1)

## 5. P1 / P2 Backlog

### 5.1 P1 (다음 사이클)

- KV cache alignment 구현 (CacheControl)
- LLM-based triage option (Ollama or API)
- Provider 추가 (Gemini, Bedrock, Ollama local)
- AST compressor (Python 우선)
- Quota tracking (per-provider, per-user)
- Stream 안정화 (large payload)

### 5.2 P2 (out-of-scope)

- ML compressor 모델 학습 (Kompress류)
- Full RBAC hierarchy (admin/keymaster/inferrer)
- Admin UI (HTML/JS)
- `learn` failure mining
- Output token shaping (verbosity steering)
- Multi-user / shared CCR store
- Image compression
- Cross-agent memory (headroom 차용)

## 6. 결정 항목 (TASK-001 lock-in)

### Q1. Python-only vs Python+Rust 처음부터 분리 — **확정**

- **결정**: **Python-only (1차)**. FastAPI + uvicorn.
- **이유**: P0의 hot path는 provider routing + content type detection + trivial compression이라 Python으로 충분. token-router가 777 LOC Python으로 동작하는 게 증거. headroom의 32k LOC Rust는 ML 모델 + 6 compressor + cache stabilization 까지 다 박았기 때문에 필요했던 규모.
- **P2**: hot path가 실제 병목이 되는 지점이 발견되면 그때 PyO3/maturin 도입. 1차 release 후 회고에서 결정.

### Q2. CCR-lite storage 백엔드 — **확정**

- **결정**: **Redis + Mongo 조합**. multi-user 가정.
- **이유**:
  - **Redis (hot path)**: request 단위 cache + rate limit counter + session lookup + KV cache hint + CCR-lite read-through cache. TTL 적합, microsecond latency.
  - **Mongo (cold path)**: CCR-lite 영구 저장 + user metadata + provider config + conversation log + audit log. 영구성 + queryable + flexible schema.
  - multi-user 가정 → 모든 data path 에 `user_id` 필터, Redis key prefix `user:{user_id}:...`, Mongo query에 `user_id` 조건 필수.
- **Multi-user 영향**:
  - Auth layer (Bearer token) + RBAC (admin/user 2단) 도입.
  - Per-user rate limit, per-user CCR store, per-user provider config.
  - Tenant isolation이 모든 layer 의 1차 concern.

### MVP scope 갱신 (Q1/Q2 반영)

§4 MVP scope에 다음 2개 component 추가됨:
- **Auth layer (Bearer token + RBAC)**: Redis session cache + Mongo users collection
- **Rate limit (Redis)**: per-user, per-minute sliding window

CCR-lite spec 변경: SQLite 단일 파일 → **Redis (hot) + Mongo (cold)**. architecture.md §3 에서 data layer 명세.

P2 → P1 이동:
- KV cache alignment (Redis cache hint 도입하면서 함께 구현)

## 7. Cross-reference

| 항목 | 1차 출처 | 2차 출처 (코드) |
|---|---|---|
| Pipeline pattern | headroom README §"How it works" | `crates/headroom-core/src/transforms/pipeline/` |
| CacheControl abstraction | headroom `headroom/cache/base.py` | `cache/{anthropic,openai,google}.py` |
| CCR concept | headroom README §"CCR reversible" | `crates/headroom-core/src/ccr/` |
| OpenAI-compat proxy | tokenrouter README §"Use One Endpoint" | `pkg/proxy/server.go` |
| RBAC + 임시 키 | tokenrouter README §"Token hierarchy" | `cmd/toro/main.go` |
| 3-mode triage | token-router README §"Three routing modes" | `scripts/router.py` §"VALID_MODES" |
| Fixture regression | token-router README §"Regression Tests" | `tests/router-tests.json` + `scripts/run_router_tests.py` |

## 다음에 읽을 문서

- [Project Profile](../PROJECT_PROFILE.md) — 프로젝트 개요/명령
- [Session Handoff](../../ai-workflow/memory/active/session_handoff.md) — 다음 세션 시작 컨텍스트
- [Daily Backlog](../../ai-workflow/memory/active/backlog/2026-06-24.md) — 2026-06-24 작업 상세 기록