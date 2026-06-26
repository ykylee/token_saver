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
- 다음 세션 시작: TASK-002-5 Provider router + Provider Registry (test_connection + list_models + Redis cache + Mongo providers collection) + OpenAI/Anthropic/Ollama/vLLM clients + RedisSessionStore replace InMemory

## Work Status

- TASK-001 라우터 아키텍처 및 scope 정의: **done** (commits `e921182` `91fef89` `6bbb7c9`)
- TASK-002-1 project skeleton: **done** (commit `d50fb97`)
- TASK-002-2 OpenAI-compatible endpoints (mock): **done** (commit `cba86bc`)
- TASK-002-3 Bearer token auth + RBAC: **done** (commit `0f97b4c`)
- TASK-002-4 Mongo users collection + admin seed + indexes: **done** (commit pending in 2026-06-26 cycle)
  - `Settings.user_store_backend: Literal["memory", "mongo"] = "memory"` 추가 — operator env 가 backend 선택
  - `auth/repository.py` 갱신:
    - `UserStore` Protocol 에 `upsert(email, password, role)` 추가 (admin 경로)
    - `InMemoryUserStore.upsert` — idempotent update (admin password rotate), `_seed_admin` 는 `admin_email/password` None 일 때 skip
    - `MongoUserStore` 신규 — `AsyncIOMotorClient` + `ensure_indexes()` (email unique + role + created_at) + `_id` 보존 update + `close()`
    - 공통: `_new_user_id()`, `_to_record()`, `_doc_to_record()` — wire format 정합
  - `auth/factory.py` 신규 — `build_user_store(settings) -> (store, optional mongo_client)`. backend switch 단일 진입점. `UnknownUserStoreBackendError` 로 typo 차단
  - `auth/seed.py` 신규 — `seed_admin(settings, store)`. strict-idempotent (existing admin password 절대 overwrite 안 함). `SeedAdminSkipped` 로 "skip" vs "created" 분기
  - `proxy/app.py` 갱신:
    - `build_user_store(settings)` 호출 — user_store + mongo_client 동시 획득
    - FastAPI lifespan: startup 에서 `MongoUserStore.ensure_indexes` + `seed_admin` (InMemory 는 no-op). shutdown 에서 `mongo_client.close()`
    - `app.state.session_store / user_store / mongo_client` attach
  - `pyproject.toml` — dev dep `mongomock-motor>=0.0.36` 추가 (호환성 검증 완료)
  - `.env.example` + `docker-compose.yml` — `TOKEN_SAVER_USER_STORE_BACKEND` 환경 변수 노출 (docker 는 `mongo`, local/test 는 `memory` 기본)
  - tests/test_user_store_factory.py — backend 선택 2 + unknown backend raise 1 (model_construct 로 Literal 우회)
  - tests/test_mongo_user_store.py — mongomock-motor CRUD 6 + unique index 1 (ensure idempotent / case-insensitive / _id 보존)
  - tests/test_seed_admin.py — empty store + seed_admin 직접 호출 5 (created / strict-idempotent / no-config / partial-config / email case)
  - tests/test_import.py — 21 → 23 modules parametrised
- TASK-002 MVP 1차 cycle: **in_progress** (TASK-002-5 .. TASK-002-7 remaining)

## Key Changes (2026-06-26, 누적)

- bootstrap (Standard AI Workflow v0.9.5-beta, minimax-code harness) — commit `bad7985`
- TASK-001 reference 분석 + cherry-pick 매트릭스 + MVP scope — commit `e921182`
- TASK-001 lock-in 결정 반영 + docs/architecture.md 작성 — commit `91fef89`
- TASK-001 follow-up: local LLM = 별도 서버 + Models API discovery spec — commit `6bbb7c9`
- GitHub origin remote 연결 (https://github.com/ykylee/token_saver.git) + 4 commit push
- TASK-002-1 project skeleton — commit `d50fb97`
- TASK-002-2 OpenAI-compatible endpoints (mock) — commit `cba86bc`
- TASK-002-3 Bearer token auth + RBAC — commit `0f97b4c`
- TASK-002-4 Mongo users collection + admin seed + indexes — commit pending

## Next Actions

- [ ] TASK-002-5: Provider router + Provider Registry (test_connection + list_models + Redis cache + Mongo providers collection) + OpenAI/Anthropic/Ollama/vLLM clients (real forwarding via httpx, e2e fixture via respx) + RedisSessionStore replace InMemorySessionStore
- [ ] TASK-002-6: Fixture-based regression test (1 case: OpenAI pass-through with mock provider) + SSE streaming passthrough
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
- **Build verification (TASK-002-1/2/3/4)**: ruff clean, mypy clean (24 source files), pytest 77 passed

## 환경 노트

- 작업 디렉토리: `/Users/yklee/repos/token_saver`
- venv: `.venv/` (Python 3.14.5, editable install, dev extras)
- MCP transport_ready: false — TASK-002 와 함께 enable 검토
- git remote: `origin` → `https://github.com/ykylee/token_saver.git` (tracking main)
- TestClient deprecation warning: starlette 1.x 의 httpx → httpx2 마이그레이션 검토 필요