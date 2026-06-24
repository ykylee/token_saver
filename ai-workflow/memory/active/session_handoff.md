<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: stable (TASK-001 done, TASK-002 ready)
- Updated: 2026-06-24
- Related docs: [Project Profile](../../docs/PROJECT_PROFILE.md), [Concept](../../docs/concepts/token-saver-concept.md), [Architecture](../../docs/architecture.md), [Work Backlog](./work_backlog.md)

## Current Focus

- TASK-001 done — concept + architecture 영구 보존 완료, Q1/Q2 lock-in 완료
- TASK-002 planned — MVP 1차 cycle (HTTP proxy skeleton + auth + provider router + fixture regression)
- 다음 세션 시작: TASK-002-1 project skeleton (`pyproject.toml`, `src/`, `tests/`, `docker-compose.yml`)

## Work Status

- TASK-001 라우터 아키텍처 및 scope 정의: **done**
  - 3 reference 분석 + cherry-pick 매트릭스 + MVP scope: done
  - Q1/Q2 lock-in (Python-only, Redis+Mongo multi-user): done
  - docs/concepts/token-saver-concept.md 영구 보존: done
  - docs/architecture.md (component / lifecycle / data layer / RBAC / deployment): done
- TASK-002 MVP 1차 cycle: **planned** (구현 진입 직전)

## Key Changes (2026-06-24, 누적)

- bootstrap (Standard AI Workflow v0.9.5-beta, minimax-code harness) — commit `bad7985`
- TASK-001 reference 분석 + cherry-pick 매트릭스 + MVP scope — commit `e921182`
- TASK-001 lock-in 결정 반영 + docs/architecture.md 작성 — pending commit

## Next Actions

- [ ] TASK-002-1: project skeleton — `pyproject.toml`, `src/token_saver/{proxy,auth,...}/`, `tests/fixtures/`, `docker-compose.yml`
- [ ] TASK-002-2: FastAPI app + OpenAI-compatible `/v1/chat/completions` endpoint (mock response)
- [ ] TASK-002-3: Bearer token auth + Redis session lookup
- [ ] TASK-002-4: Mongo connection + users collection + admin seed script
- [ ] TASK-002-5: Provider router + OpenAI client (real forwarding, e2e fixture)
- [ ] TASK-002-6: Fixture-based regression test (1 case: OpenAI pass-through with mock provider)
- [ ] TASK-002-7: CLI `token-saver serve` + `token-saver config show`
- [ ] TASK-002 done → first release v0.1.0

## Risks & Blockers

- **Q1/Q2 결정**: resolved (Python-only 1차, Redis+Mongo multi-user)
- **MVP scope 적정성**: headroom 32k vs token-router 921 LOC 차이에서 우리 ~2,800 LOC 예산 적절한지 TASK-002 회고에서 검증
- **Redis/Mongo 운영 부담**: multi-user 가정 → docker-compose 로컬 + production P1 (managed Redis/Atlas). 1차 release 는 docker-compose 만으로 충분.
- **OpenAI/Anthropic API rate limit**: proxy가 단일 client보다 2-3x traffic 발생 가능 → burst protection P1
- **Bearer token rotation**: 1차 release 는 TTL 1h 단일. rotation API 는 P1.

## Reference for next session

- **핵심 결정 (TASK-001 lock-in)**:
  - Q1: Python-only 1차 (FastAPI + uvicorn). Rust core는 P2.
  - Q2: Redis (hot) + Mongo (cold) 조합. multi-user 처음부터 가정.
- **Component map**: `docs/architecture.md` §2
- **Request lifecycle (11 steps)**: `docs/architecture.md` §3
- **Redis schema**: `docs/architecture.md` §4.1 (6 key patterns + TTL)
- **Mongo schema**: `docs/architecture.md` §4.2 (5 collections + indexes)
- **RBAC matrix**: `docs/architecture.md` §5.2
- **docker-compose**: `docs/architecture.md` §6.1
- **LOC 예산 ~2,800**: `docs/architecture.md` §2.1

## 환경 노트

- 작업 디렉토리: `/Users/yklee/repos/token_saver`
- Python venv 미생성 — TASK-002-1 에서 `python3 -m venv .venv && pip install -e ".[dev]"`
- MCP transport_ready: false — TASK-002 와 함께 enable 검토
- git 상태: main, 2 commit (bad7985 + e921182) — TASK-002 시작 시 본 session commit 먼저 필요