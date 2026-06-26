<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: stable (TASK-001 done, TASK-002-1 done, TASK-002-2 ready)
- Updated: 2026-06-26
- Related docs: [Project Profile](../../docs/PROJECT_PROFILE.md), [Concept](../../docs/concepts/token-saver-concept.md), [Architecture](../../docs/architecture.md), [Work Backlog](./work_backlog.md)

## Current Focus

- TASK-001 done — concept + architecture 영구 보존 완료, Q1/Q2 lock-in 완료
- TASK-002-1 done — project skeleton
- TASK-002-2 done — OpenAI-compatible endpoints (mock)
- TASK-002-3 done — Bearer token auth + session store + RBAC + admin-only `/admin/health` gate
- TASK-002-4 done — Mongo users collection + admin seed + indexes + factory (memory/mongo backend switch)
- TASK-002-5-a done — Provider core (BaseProvider Protocol + OpenAI-compat + Anthropic impls + Registry + Router + chat forward via httpx) + respx fixture
- 다음 세션 시작: TASK-002-5-b ProviderStore + CRUD routes (test/add/list/refresh/delete) + RBAC + Mongo providers collection + Redis models cache + RedisSessionStore

## Work Status

- TASK-001 라우터 아키텍처 및 scope 정의: **done** (commits `e921182` `91fef89` `6bbb7c9`)
- TASK-002-1 project skeleton: **done** (commit `d50fb97`)
- TASK-002-2 OpenAI-compatible endpoints (mock): **done** (commit `cba86bc`)
- TASK-002-3 Bearer token auth + RBAC: **done** (commit `0f97b4c`)
- TASK-002-4 Mongo users collection + admin seed + indexes: **done** (commit `2f069d7`)
- TASK-002-5-a Provider core + chat real forward: **done** (commit pending in 2026-06-26 cycle)
  - `provider/base.py` 신규 — `BaseProvider` Protocol + `ProviderConfig` / `ProviderTestResult` / `ModelInfo` / `InvokeOptions` / `ProviderError` (Connection/Response/Config) + `unwrap_text_content` / `usage_from_openai_compat` / `usage_from_anthropic` helper
  - `provider/openai_compat.py` 신규 — `OpenAICompatProvider` (httpx async). OpenAI native + Ollama + vLLM 모두 처리 (wire format 동일). `test_connection` (GET /v1/models) + `list_models` + `invoke` (POST /v1/chat/completions)
  - `provider/anthropic.py` 신규 — `AnthropicProvider`. system message 를 top-level `system` field 로 lift, `max_tokens` default 1024, `input_tokens`/`output_tokens` → `prompt_tokens`/`completion_tokens` 변환
  - `provider/registry.py` 신규 — `ProviderRegistry` (id → BaseProvider map) + `register` / `unregister` / `get` / `find_by_type` / `find_default` / `all` / `aclose` + `from_config` factory (openai/ollama/vllm → OpenAICompatProvider, anthropic → AnthropicProvider, unknown → `UnknownProviderTypeError`)
  - `provider/router.py` 신규 — `ProviderRouter`. prefix 파싱 (`openai/gpt-4o-mini` → (openai, gpt-4o-mini)) + 명시적 hint + registry default resolution + `AmbiguousProviderError` / `NoProviderAvailableError`
  - `provider/deps.py` 신규 — `get_provider_registry` FastAPI dependency
  - `models.py` 보강 — `ProviderType` / `ProviderTestRequest` / `ProviderTestResult` / `ProviderCreateRequest` / `ProviderRecord` / `ProviderInfo` / `ModelsRefreshResult` (TASK-002-5-b wire shape 미리 추가)
  - `proxy/routes/chat_completions.py` 갱신 — mock path 제거 (MOCK_HEADER, _build_mock_completion, _estimate_tokens 삭제), `ProviderRouter.invoke` 호출. 400 ambiguous / 502 upstream / 503 no_provider_available / 422 validation / 400 stream_not_supported
  - `proxy/app.py` 갱신 — `app.state.provider_registry = ProviderRegistry()`, lifespan shutdown 에 `provider_registry.aclose()` 호출
  - `tests/test_chat_completions.py` 갱신 — mock 관련 test 제거, validation / stream / 503 (empty registry) / 503 (unknown prefix) 6 test 유지
  - `tests/test_chat_completions_forwarding.py` 신규 — respx fixture 로 real forwarding wire path 검증 7 (happy / sampling knobs / provider prefix / extra fields / 5xx upstream / connection refused / ambiguous)
- TASK-002 MVP 1차 cycle: **in_progress** (TASK-002-5-b .. TASK-002-7 remaining)

## Key Changes (2026-06-26, 누적)

- bootstrap (Standard AI Workflow v0.9.5-beta, minimax-code harness) — commit `bad7985`
- TASK-001 reference 분석 + cherry-pick 매트릭스 + MVP scope — commit `e921182`
- TASK-001 lock-in 결정 반영 + docs/architecture.md 작성 — commit `91fef89`
- TASK-001 follow-up: local LLM = 별도 서버 + Models API discovery spec — commit `6bbb7c9`
- GitHub origin remote 연결 (https://github.com/ykylee/token_saver.git) + 4 commit push
- TASK-002-1 project skeleton — commit `d50fb97`
- TASK-002-2 OpenAI-compatible endpoints (mock) — commit `cba86bc`
- TASK-002-3 Bearer token auth + RBAC — commit `0f97b4c`
- TASK-002-4 Mongo users collection + admin seed + indexes — commit `2f069d7`
- TASK-002-5-a Provider core + chat real forward — commit pending

## Next Actions

- [ ] TASK-002-5-b: ProviderStore (in-memory + Mongo) + CRUD routes (POST /v1/providers/test + /v1/providers + GET /v1/providers + POST /{id}/models/refresh + DELETE /{id}) + RBAC (own-only / all) + Redis models cache + RedisSessionStore
- [ ] TASK-002-6: Fixture-based regression test (full end-to-end via Provider CRUD + chat forward) + SSE streaming passthrough
- [ ] TASK-002-7: CLI `token-saver serve` + `provider test` + `provider add` + `provider list` + `provider refresh` + `provider delete`
- [ ] TASK-002 done → first release v0.1.0
- [ ] TASK-002-4: Mongo connection + users collection + admin seed script
- [ ] TASK-002-5: Provider router + Provider Registry (test_connection + list_models + cache) + OpenAI client (real forwarding, e2e fixture)
- [ ] TASK-002-6: Fixture-based regression test (1 case: OpenAI pass-through with mock provider)
- [ ] TASK-002-7: CLI `token-saver serve` + `provider test` + `provider add` + `provider list` + `provider refresh` + `provider delete`
- [ ] TASK-002 done → first release v0.1.0

## Risks & Blockers

- **Q1/Q2 결정**: resolved (Python-only 1차, Redis+Mongo multi-user)
- **MVP scope 적정성**: headroom 32k vs token-router 921 LOC 차이에서 우리 ~3,000 LOC 예산 적절한지 TASK-002 회고에서 검증
- **PEP 440 표기 fix**: pyproject `0.1.0-alpha` → `0.1.0a0` (wheel normalize 시 `-` 이 사라져서 version drift risk). 이후 release 도 PEP 440 strict.
- **TestClient deprecation warning**: starlette 1.x 의 httpx 사용 deprecation. FastAPI 0.138 / starlette 1.3.x 영향. 1차 release 전 httpx2 마이그레이션 검토.
- **OpenAI/Anthropic API rate limit**: proxy가 단일 client보다 2-3x traffic 발생 가능 → burst protection P1
- **Bearer token rotation**: 1차 release 는 TTL 1h 단일. rotation API 는 P1.

## Reference for next session

- **핵심 결정 (TASK-001 lock-in + follow-up)**:
  - Q1: Python-only 1차 (FastAPI + uvicorn). Rust core는 P2.
  - Q2: Redis (hot) + Mongo (cold) 조합. multi-user 처음부터 가정.
  - Local LLM: 별도 서버 + URL/port/API key + Models API 동적 discovery
- **Component map**: `docs/architecture.md` §2 (Provider Registry 추가)
- **Request lifecycle (11+ steps)**: `docs/architecture.md` §3
- **Provider registration flow**: `docs/architecture.md` §4.2 (POST /v1/providers/test + /providers)
- **Redis schema**: `docs/architecture.md` §4.1 (7 key patterns + TTL)
- **Mongo schema**: `docs/architecture.md` §4.2 (5 collections + indexes)
- **RBAC matrix**: `docs/architecture.md` §5.2 (13 endpoints)
- **docker-compose**: `docs/architecture.md` §6.1 (local LLM 제거, 별도 서버 가동)
- **LOC 예산 ~3,000**: `docs/architecture.md` §2.1
- **TASK-002-1 skeleton LOC**: 507 (src 251 + tests 256) — ~17% of budget
- **TASK-002-2 cumulative LOC**: 1240 (src ~500 + tests ~740) — ~41% of budget
- **TASK-002-3 cumulative LOC**: 2098 (src ~870 + tests ~1230) — ~70% of budget
- **TASK-002-4 cumulative LOC**: 2701 (src ~1180 + tests ~1520) — ~90% of budget
- **TASK-002-5-a cumulative LOC**: 3987 (src ~1620 + tests ~2370) — **133% of budget** (over)
- **Build verification (TASK-002-1..5-a)**: ruff clean, mypy clean (30 source files), pytest 82 passed

## 환경 노트

- 작업 디렉토리: `/Users/yklee/repos/token_saver`
- venv: `.venv/` (Python 3.14.5, editable install, dev extras)
- MCP transport_ready: false — TASK-002 와 함께 enable 검토
- git remote: `origin` → `https://github.com/ykylee/token_saver.git` (tracking main)
- TestClient deprecation warning: starlette 1.x 의 httpx → httpx2 마이그레이션 검토 필요