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
- TASK-002-1 done — project skeleton (pyproject.toml + src/ + tests/ + docker-compose + Dockerfile + README + venv install + 16 smoke tests)
- 다음 세션 시작: TASK-002-2 FastAPI app + OpenAI-compatible `/v1/chat/completions` endpoint (mock response)

## Work Status

- TASK-001 라우터 아키텍처 및 scope 정의: **done**
  - 3 reference 분석 + cherry-pick 매트릭스 + MVP scope: done
  - Q1/Q2 lock-in (Python-only, Redis+Mongo multi-user): done
  - docs/concepts/token-saver-concept.md 영구 보존: done
  - docs/architecture.md (component / lifecycle / data layer / RBAC / deployment): done
- TASK-002-1 project skeleton: **done** (commit pending in 2026-06-26 cycle)
  - pyproject.toml (PEP 440, deps FastAPI/uvicorn/httpx/redis/motor/pydantic/cryptography/argon2-cffi/python-ulid/click/structlog, dev extras pytest+respx+ruff+mypy)
  - src/token_saver/ 7 sub-packages (proxy/auth/ratelimit/detector/compressor/provider/ccr) + cli.py + config.py + __version__.py
  - tests/conftest.py + test_import.py (16 tests covering version/settings/app factory/healthz/public module imports/package layout vs pyproject)
  - docker-compose.yml (Redis 7 + Mongo 7 + token-saver service, healthchecks)
  - Dockerfile (multi-stage python:3.12-slim, non-root user, /healthz healthcheck)
  - .env.example + README.md
  - venv + `pip install -e ".[dev]"` verified, ruff/mypy/pytest all green
- TASK-002 MVP 1차 cycle: **in_progress** (TASK-002-2 .. TASK-002-7 remaining)

## Key Changes (2026-06-26, 누적)

- bootstrap (Standard AI Workflow v0.9.5-beta, minimax-code harness) — commit `bad7985`
- TASK-001 reference 분석 + cherry-pick 매트릭스 + MVP scope — commit `e921182`
- TASK-001 lock-in 결정 반영 + docs/architecture.md 작성 — commit `91fef89`
- TASK-001 follow-up: local LLM = 별도 서버 + Models API discovery spec — commit `6bbb7c9`
- GitHub origin remote 연결 (https://github.com/ykylee/token_saver.git) + 4 commit push
- TASK-002-1 project skeleton (pyproject + src + tests + docker-compose + Dockerfile + README + smoke) — commit pending

## Next Actions

- [ ] TASK-002-2: FastAPI app + OpenAI-compatible `/v1/chat/completions` endpoint (mock response, no upstream call yet)
- [ ] TASK-002-3: Bearer token auth + Redis session lookup
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
- **Build verification (TASK-002-1)**: ruff clean, mypy clean (12 source files), pytest 16 passed

## 환경 노트

- 작업 디렉토리: `/Users/yklee/repos/token_saver`
- venv: `.venv/` (Python 3.14.5, editable install, dev extras)
- MCP transport_ready: false — TASK-002 와 함께 enable 검토
- git remote: `origin` → `https://github.com/ykylee/token_saver.git` (tracking main)
- TestClient deprecation warning: starlette 1.x 의 httpx → httpx2 마이그레이션 검토 필요