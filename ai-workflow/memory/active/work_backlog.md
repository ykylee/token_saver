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

- [ ] **TASK-001: 라우터 아키텍처 및 scope 정의** — 분석 단계 완료 (reference 3종 비교 + cherry-pick 매트릭스 + MVP scope), lock-in 대기 (Q1, Q2)
  - 분석 결과: `docs/concepts/token-saver-concept.md` §3, §4
  - 결정 대기: Q1 Python-only vs Rust 분리, Q2 CCR-lite storage 백엔드

### Done

- [x] **TASK-000: workflow bootstrap** — Standard AI Workflow v0.9.5-beta 도입 (minimax-code harness), commit `bad7985`
  - 결과: ai-workflow/, .MiniMax/, MiniMax.md, MiniMax_config.example.json, docs/PROJECT_PROFILE.md

### Blocked

- 🔒 **TASK-001 lock-in** — Q1/Q2 사용자 결정 대기
