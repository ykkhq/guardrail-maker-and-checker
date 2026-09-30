# Guardrail Studio

Design prompt-guardrail flows as a graph, and run them on Kong AI Gateway (managed by Konnect).

Status: all parts from the plan are working: the **engine**, the **compiler**, **Konnect deploy**, the **Web UI** and the **Docker stack**. The `jp-support` sample runs end to end on the `guardrail-service` control plane (US region).

## Web UI

Open **http://localhost:13000** once the stack is running.

- **Canvas:** drag guardrails from the palette and connect `Prompt In → … → LLM → Response Out`. Condition nodes have `true`/`false` outputs. Node color shows where each step runs: indigo is a Kong plugin, teal is the guardrail engine (via DataKit), and orange is control.
- **Inspector:** a settings form generated from each node's JSON Schema. Condition rules pick detector nodes from the canvas. Secret fields ask for `{vault://…}` references.
- **Validation:** runs as you edit. Errors and warnings show as badges on the nodes and in the *Issues* tab. Deploy is disabled while there are errors.
- **Config:** Kong's execution order and the compiled decK state.
- **Save / Deploy / Versions:** Deploy saves the pipeline, compiles it, pushes it to Konnect and records a version. *Versions* rolls back to an earlier version and redeploys it.
- **Playground:**
  - *Dry run* runs the canvas as it is now through the engine, with no deploy needed.
  - *Send via Kong* sends the request through the deployed pipeline and the LLM.

  Both color the path the prompt took on the canvas.

For frontend development: `cd web && npm install && npm run dev` (http://localhost:5173). It proxies `/api` to studio-api on :18200.

Deploy (the token is read from a file and never printed):

```bash
cd studio-api
KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.deploy ../samples/jp-support.json   # add --create --region us on first run
KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.deploy ../samples/jp-support.json --undeploy
```

## Run the stack (Docker)

```bash
cd studio-api
KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.deploy ../samples/jp-support.json --create --region us
KONNECT_PAT_FILE=~/.kong/kpat uv run python -m guardrail_studio.bootstrap   # DP cert + kong/konnect.env
cd .. && scripts/up.sh                                                        # OpenAI key from ~/.openai/creds

curl -s localhost:18000/pipelines/jp-support -H 'content-type: application/json' \
  -d '{"messages":[{"role":"user","content":"私のメールは user@example.com です"}]}'
```

| Service | Port | Notes |
|---|---|---|
| `kong-dp` | 18000 (proxy), 18100 (status) | Kong Gateway 3.14 data plane connected to Konnect with a pinned client certificate. |
| `guardrail-engine` | 18080 | Preloads Presidio (ja) and the sentiment model. Llama Guard uses the host's Ollama through `OLLAMA_HOST`. |
| `studio-api` | 18200 | Pipelines (SQLite volume), compiler, Konnect deploy, playground. The Konnect PAT comes from `~/.kong/kpat` via `scripts/up.sh`. |
| `web` | 13000 | The UI (nginx). Proxies `/api` to studio-api. |
| `ollama` | – | Optional (`--profile ollama`). Use it if there is no Ollama on the host. |

Always (re)start containers with `scripts/up.sh [service…]`, not plain `docker compose up`. Otherwise the secret variables are empty, and `studio-api` loses Konnect access.

The OpenAI key is passed to `kong-dp` as the environment variable `OPENAI_AUTH_HEADER` (`Bearer <key>`), which `{vault://env/OPENAI_AUTH_HEADER}` references. The DP certificate and key are passed the same way, as `KONG_CLUSTER_CERT` and `KONG_CLUSTER_CERT_KEY`. None of these are written to disk, but `docker inspect` shows them.

## Layout

| Path | What it is |
|---|---|
| `common/guardrail_common/` | Shared by all parts. `graph.py`: the pipeline graph (React Flow shape). `catalog.py`: node types with JSON Schemas. `conditions.py`: condition rules. |
| `engine/guardrail_engine/` | FastAPI service that runs custom detectors and control nodes. Kong DataKit calls it. |
| `studio-api/guardrail_studio/` | FastAPI backend. `compiler/` turns a graph into Kong entities (service, route, plugins) or a decK file. `konnect.py` deploys them. `store.py` keeps pipelines and versions. `main.py` serves the UI API. |
| `web/` | Vite + React + React Flow UI (`src/App.tsx`, `src/components/`). |
| `samples/` | Example pipelines (`jp-support.json`). |

## How a graph runs on Kong

- **Native nodes** (`ai_prompt_guard`, `ai_sanitizer`, `ai_azure_content_safety`, …) each become a Kong plugin on the pipeline's route (`/pipelines/{slug}`). Request-phase plugins use dynamic ordering (`ordering.before.access`) so they run in the order drawn on the canvas.
- **Custom and control nodes** (Llama Guard, Presidio PII, sentiment/kasuhara, Laya, keyword, condition, block) in one phase form a *segment*. One DataKit plugin sends the segment spec plus the chat body to `POST /v1/segments/execute`. The engine returns either `allow` with the rewritten (masked) body, or `block` with a status. DataKit then writes the body upstream or exits with that status.
- **LLM** becomes `ai-proxy-advanced`.

Kong runs one instance of each plugin per route. That gives two canvas rules, and `validate` checks both:
- Each native plugin type can be used only once.
- In each phase, all custom/control nodes must be next to each other. A native plugin cannot sit between them.

A native node also cannot sit inside a branch, because it runs on every request.

## Detector semantics

Each detector returns a `DetectorResult`: `detected`, `score`, `label`, `flags`, `text`, `entities`, `error`.

Per node you configure:
- `on_detect`: `block`, `flag`, or `mask` (for PII nodes).
- `fail_mode`: `closed` (the default) returns 503 when the detector errors; `open` continues. The prototype always failed open.

Condition nodes read earlier results with structured rules, for example `{"node": "sentiment", "field": "flags.kasuhara", "op": "eq", "value": true}`, and route to their `true` or `false` output.

Checks run on the last user message. Masking applies to every user message, so PII in the history is masked too.

## Development

```bash
uv sync --all-packages           # light deps only (no ML models)
uv run pytest                    # 46 tests; real-model tests are skipped
uv run pytest -m models          # real models: needs `uv sync --extra models` (in engine/), spaCy ja_core_news_trf, Ollama
UPDATE_GOLDEN=1 uv run pytest studio-api/tests/test_compile.py   # regenerate the decK golden file after an intended change

uv run uvicorn guardrail_engine.main:app --port 8080      # engine
uv run uvicorn guardrail_studio.main:app --port 8000      # studio-api
```

Engine environment variables:
- `GUARDRAIL_PRELOAD=presidio_pii,sentiment_kasuhara`: load these models at startup.
- `PRESIDIO_LANGUAGES=ja,en`: languages Presidio loads.

studio-api environment variables:
- `GUARDRAIL_ENGINE_URL`: engine URL written into the DataKit `call` node.
- `STUDIO_CORS_ORIGINS`: allowed origins for the Web UI.

## Changes from the guardrail_agent prototype

- The sentiment model default is now `koheiduck/bert-japanese-finetuned-sentiment`. `cl-tohoku/bert-base-japanese-v3` has no classification head, so its labels were random.
- Llama Guard calls the Ollama REST API directly and honors `fail_mode`.
- Presidio's language comes from node config.
- Laya results are returned as flags that condition nodes can branch on.

## To verify on a real data plane (Phase 0 spike)

- DataKit `output: service_request.body` rewrites the body before `ai-proxy-advanced` reads it.
- Dynamic ordering between `datakit` and the AI guard plugins at runtime (Konnect accepts the config).
- One DataKit plugin that carries both request- and response-phase nodes.
- Field names in the native plugin schemas (`catalog.py`) for the pinned Kong version.
