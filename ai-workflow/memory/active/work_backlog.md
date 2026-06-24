<!-- standard-ai-workflow-kit: v0.9.5-beta -->

# 작업 백로그 인덱스

- 문서 목적: 프로젝트의 모든 작업 항목과 날짜별 백로그 링크를 관리한다.
- 범위: 전체 태스크 목록, 우선순위, 진행 상태, 날짜별 기록 연결
- 대상 독자: 개발자, AI 에이전트, 프로젝트 매니저
- 상태: stable
- 최종 수정일: 2026-06-24
- 관련 문서: [세션 인계](./session_handoff.md), [프로젝트 프로파일](../../docs/PROJECT_PROFILE.md)

## 1. 운영 원칙
1. 세션 시작 시 인덱스와 최신 백로그 확인
2. 세션 종료 전 인덱스 및 Handoff 갱신
3. 모든 작업 상태는 날짜별 백로그에 기록

## 2. 날짜별 백로그
- [2026-06-24](./backlog/2026-06-24.md)

## 3. 전체 작업 상태 요약

### In Progress

(현재 in-progress task 없음. TASK-002 planned, 구현 진입 직전.)

### Planned

- [ ] **TASK-002: MVP 1차 cycle — HTTP proxy skeleton + auth + provider router + Provider Registry + fixture regression**
  - spec: `docs/architecture.md` §2-§6
  - LOC 예산: ~3,000 (proxy 400 + auth 200 + ratelimit 150 + detector 300 + compressor 400 + provider 650 + ccr 350 + cli 250 + tests 300)
  - sub-task breakdown (TASK-002-1 ~ TASK-002-7): session_handoff.md §Next Actions 참조
  - 권장 페이스: 1 session = 1 sub-task (~150-400 LOC, 1 chapter 패턴)
  - **Provider Registry sub-component** (TASK-002-5 안에서):
    - BaseProvider Protocol + test_connection() + list_models()
    - OpenAI / Anthropic / Ollama / vLLM impl 모두 동일한 Protocol 구현
    - Redis cache (TTL 1h) + Mongo models_cache 영구 저장

### Done

- [x] **TASK-001: 라우터 아키텍처 및 scope 정의** — commit `e921182` + pending commit
  - 3 reference 비교 (headroom 49.3k★ / tokenrouter 15★ / token-router 72★)
  - cherry-pick 매트릭스 (~50% applicable)
  - MVP scope P0/P1/P2 도출
  - Q1/Q2 lock-in: Python-only 1차, Redis+Mongo multi-user
  - 결과: `docs/concepts/token-saver-concept.md` + `docs/architecture.md`
- [x] **TASK-000: workflow bootstrap** — Standard AI Workflow v0.9.5-beta 도입 (minimax-code harness), commit `bad7985`
  - 결과: ai-workflow/, .MiniMax/, MiniMax.md, MiniMax_config.example.json, docs/PROJECT_PROFILE.md

### Blocked

(없음)
