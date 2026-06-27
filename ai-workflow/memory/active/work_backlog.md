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
- [2026-06-26](./backlog/2026-06-26.md)
- [2026-06-27](./backlog/2026-06-27.md)

## 3. 전체 작업 상태 요약

### In Progress

(현재 in-progress task 없음. TASK-002-6 진입 직전.)

### Planned

- [ ] **TASK-002: MVP 1차 cycle — HTTP proxy skeleton + auth + provider router + Provider Registry + fixture regression**
  - spec: `docs/architecture.md` §2-§6
  - LOC 예산: ~3,000 (proxy 400 + auth 200 + ratelimit 150 + detector 300 + compressor 400 + provider 650 + ccr 350 + cli 250 + tests 300)
  - **현실 누적: 7198 LOC (240% of budget)** — TASK-002-5-b 가 architecture §4.4 encryption + multi-tenant + redis cache + redis session + CRUD 를 다 들여서 예산 큰폭 초과. Retrospective cleanup (4 phase) 대상 (TASK-002-7 후).
  - sub-task breakdown (TASK-002-1 ~ TASK-002-7): session_handoff.md §Next Actions 참조
  - 권장 페이스: 1 session = 1 sub-task (~150-400 LOC, 1 chapter 패턴)
  - **TASK-002-6**: Fixture-based regression test (full end-to-end via Provider CRUD + chat forward) + SSE streaming passthrough (stream=true → real SSE iterator)
  - **TASK-002-7**: CLI `token-saver serve` + `provider test` + `provider add` + `provider list` + `provider refresh` + `provider delete`

### Done

- [x] **TASK-002-5-b: Provider store + CRUD + Redis cache + Redis session + AES-GCM crypto** — commit `7884dbd`
  - auth/crypto.py AES-GCM 보강 (encrypt_secret/decrypt_secret/derive_master_key + InvalidMasterKeyError/InvalidCiphertextError + 12-byte nonce + base64 wire format)
  - provider/store.py 신규 (ProviderStore Protocol + InMemoryProviderStore + MongoProviderStore + cipher-at-rest verification + ensure_indexes + wrong-key decrypt fail + 32-byte master_key validation)
  - provider/cache.py 신규 (ModelCatalogCache Protocol + RedisModelCatalogCache + NullModelCatalogCache + best-effort error swallow)
  - auth/tokens.py RedisSessionStore 보강 (session:{token} + JSON SessionPayload + EX-based TTL)
  - provider/factory.py 신규 (build_provider_store + build_redis_client + build_model_cache)
  - proxy/routes/providers.py 신규 (5 CRUD endpoints + RBAC + _scope_user_id helper + _http_502 mapping)
  - chat_completions.py per-user lookup (transient registry 매 request + finally registry.aclose)
  - models.py per-user ModelCard (one per enabled provider's default_model)
  - proxy/app.py lifespan (Redis/Mongo/provider_registry shutdown + provider ensure_indexes)
  - 79 신규 tests. Cumulative LOC 7198 (161 passed). ruff/mypy/pytest all green.
- [x] **TASK-002-5-a: Provider core + chat real forward** — commit `b59f0e4`
- [x] **TASK-002-4: Mongo users collection + admin seed + indexes** — commit `2f069d7`
- [x] **TASK-002-3: Bearer token auth + RBAC** — commit `0f97b4c`
- [x] **TASK-002-2: OpenAI-compatible endpoints (mock)** — commit `cba86bc`
- [x] **TASK-002-1: project skeleton** — commit `d50fb97`
- [x] **TASK-001: 라우터 아키텍처 및 scope 정의** — commit `e921182` + `91fef89` + `6bbb7c9`
- [x] **TASK-000: workflow bootstrap** — Standard AI Workflow v0.9.5-beta 도입 (minimax-code harness), commit `bad7985`
  - 결과: ai-workflow/, .MiniMax/, MiniMax.md, MiniMax_config.example.json, docs/PROJECT_PROFILE.md

### Blocked

(없음)
