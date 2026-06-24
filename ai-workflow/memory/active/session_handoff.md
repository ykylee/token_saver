<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# Session Handoff

- Purpose: Compact restore context for the next AI agent session.
- Scope: current focus, task status, key changes, next actions, risks
- Audience: AI agents, maintainers
- Status: in_progress
- Updated: 2026-06-24
- Related docs: [Project Profile](../../docs/PROJECT_PROFILE.md), [Concept](../../docs/concepts/token-saver-concept.md), [Work Backlog](./work_backlog.md)

## Current Focus

- TASK-001 라우터 아키텍처 및 scope 정의 — 분석 단계 완료, 사용자 lock-in 대기
- 결정 대기: **Q1** Python-only vs Python+Rust 분리, **Q2** CCR-lite storage 백엔드
- 분석 결과 영구 보존: `docs/concepts/token-saver-concept.md`

## Work Status

- TASK-001 라우터 아키텍처 및 scope 정의: in_progress (lock-in 대기)
- TASK-001-1 reference 분석 (headroom / tokenrouter / token-router): done
- TASK-001-2 layer-by-layer 장단점 + cherry-pick 매트릭스: done
- TASK-001-3 MVP scope (P0/P1/P2) 도출 + Q1/Q2 결정 항목 식별: done
- TASK-001-4 docs/concepts/token-saver-concept.md 영구 보존: done

## Key Changes (2026-06-24)

- bootstrap (Standard AI Workflow v0.9.5-beta, minimax-code harness) — commit `bad7985`
  - ai-workflow/, .MiniMax/, MiniMax.md, MiniMax_config.example.json, docs/PROJECT_PROFILE.md
  - mcp.json + MiniMax_config PYTHONPATH fix (workflow-source → ai-workflow)
  - .gitignore 작성 (agent runtime dirs, pycache, real config.json)
- TASK-001 reference 분석 + cherry-pick 매트릭스 + MVP scope — pending commit
  - docs/concepts/token-saver-concept.md 신규 (3 reference 비교, layer 장단점, MVP scope)
  - 3 reference repo: `~/repos/harness-refs/{headroom,tokenrouter,token-router}` (이미 clone됨)

## Next Actions

- [ ] Q1 결정 받기: Python-only 시작 vs 처음부터 Python+Rust (PyO3/maturin) 셋업
- [ ] Q2 결정 받기: CCR-lite = SQLite 단일 파일 vs in-memory + disk snapshot
- [ ] Q1/Q2 lock-in 후 TASK-001 spec 작성 (`docs/architecture.md` 초안)
- [ ] TASK-002 = MVP 구현 1차 사이클 (HTTP proxy + content type detector + 1 compressor)

## Risks & Blockers

- **TASK-001 lock-in blocker**: Q1/Q2 사용자 결정 없으면 spec 작성 불가
- LOC 규모 차이: headroom Rust 32k vs token-router Python 777 — 우리 MVP scope이 적정한지 검증 필요 (1차 release 후 회고)
- OpenAI/Anthropic API rate limit: proxy가 단일 client보다 2-3x traffic 발생할 수 있음 → burst protection 필요 (P1)

## Reference for next session

- 3 reference 의 1차 출처: https://github.com/headroomlabs-ai/headroom, https://github.com/lkarlslund/tokenrouter, https://github.com/sleeplesshan/token-router
- 로컬 clone: `~/repos/harness-refs/{headroom,tokenrouter,token-router}/`
- cherry-pick 매트릭스 + MVP scope: `docs/concepts/token-saver-concept.md` §3, §4

## 환경 노트

- 작업 디렉토리: `/Users/yklee/repos/token_saver`
- Python venv 미생성 — TASK-002 시작 시 `python3 -m venv .venv && pip install -r requirements.txt`
- MCP transport_ready: false (`.MiniMax/mcp.json`) — draft 상태. TASK-002 와 함께 enable 검토
- git 상태: main, 1 commit ahead of bootstrap (`bad7985`) — 본 session commit 대기