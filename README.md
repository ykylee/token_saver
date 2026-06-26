# Token Saver Router

OpenAI-compatible HTTP proxy with policy-driven content type handling,
pluggable compression, and CCR-lite caching.

> **Status:** `0.1.0a0` — TASK-002-1 skeleton (project layout, deps,
> FastAPI app factory, smoke tests, docker-compose). Behaviour lands
> across TASK-002-2 .. TASK-002-7. See `docs/architecture.md` for the
> full design.

## What it does (one-liner)

Sits between your AI agent (Claude Code, Codex, OpenCode, any OpenAI
SDK) and the upstream LLM. Inspects each request's content type, picks
a compression strategy (or skips it for lossless modes), caches the
result via CCR-lite, and forwards to the registered provider.

## Quick start

```bash
# 1. Local install (editable + dev extras)
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# 2. Smoke test
pytest -q

# 3. Local stack (Redis + Mongo + app)
cp .env.example .env   # edit secrets
docker compose up -d redis mongo
docker compose up token-saver
```

Once running, hit `http://localhost:8787/healthz` — should return
`{"status": "ok"}`.

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the system
design (components, request lifecycle, data layer, RBAC, deployment)
and [`docs/concepts/token-saver-concept.md`](docs/concepts/token-saver-concept.md)
for the rationale and external-reference comparisons.

Key decisions (TASK-001 lock-in):

| Decision | Choice | Why |
|---|---|---|
| Language | Python-only (1차) | P0 scope = policy engine; ML compressor fleets are P2 |
| Storage | Redis (hot) + Mongo (cold) | TTL fits Redis, queryable + durable fits Mongo |
| Multi-user | From day one | `user_id` filter on every data path |
| Auth | Bearer token + admin/user RBAC | 13 endpoints in the RBAC matrix |
| Local LLM | Separate server + URL/key + Models API discovery | Models picked dynamically, not from static config |

## Project layout

```
src/token_saver/
├── __init__.py          # public surface
├── __version__.py       # 3-tier fallback: pyproject → importlib → literal
├── cli.py               # `token-saver` console script entry
├── config.py            # Pydantic Settings (env-driven)
├── proxy/               # FastAPI app factory + OpenAI-compatible routes
├── auth/                # Bearer token middleware + RBAC + crypto
├── ratelimit/           # Redis sliding window
├── detector/            # Content type classifier (text/json/code/log)
├── compressor/          # Pluggable compressor registry
├── provider/            # BaseProvider Protocol + OpenAI/Anthropic/Ollama/vLLM
└── ccr/                 # CCR-lite (Redis read-through + Mongo store)

tests/                   # pytest, fixtures, smoke tests
docs/                    # architecture + concept docs
ai-workflow/             # Standard AI Workflow v0.9.5 bootstrap (kanban + state)
docker-compose.yml       # Redis + Mongo + token-saver
Dockerfile               # multi-stage prod image
```

## License

MIT.