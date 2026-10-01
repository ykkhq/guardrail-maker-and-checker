# Guardrail Studio

Design prompt-guardrail flows as a graph, and run them on Kong AI Gateway (managed by Konnect).

Status: all parts from the plan are working: the **engine**, the **compiler**, **Konnect deploy**, the **Web UI** and the **Docker stack**. The `jp-support` sample runs end to end on Kong AI Gateway 2.1, on the `guardrail-service` Konnect AI Gateway (US region).

## Web UI

Open **http://localhost:13000** once the stack is running.

- **Canvas:** drag guardrails from the palette and connect `Prompt In → … → LLM → Response Out`. Condition nodes have `true`/`false` outputs. Node color shows where each step runs: indigo is a Kong plugin, teal is the guardrail engine (via DataKit), and orange is control.
- **Inspector:** a settings form generated from each node's JSON Schema. Condition rules pick detector nodes from the canvas. Secret fields ask for `{vault://…}` references.
- **Validation:** runs as you edit. Errors and warnings show as badges on the nodes and in the *Issues* tab. Deploy is disabled while there are errors.
- **Config:** the stages and the compiled AI Gateway entities (model provider, policies, model).
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

curl -s localhost:18000/pipelines/jp-support/chat/completions -H 'content-type: application/json' \
  -d '{"model":"gs-jp-support","messages":[{"role":"user","content":"私のメールは user@example.com です"}]}'
```

`--create` creates a Konnect **AI Gateway** (`/v1/ai-gateways`), not a Kong Gateway control plane: the AI Gateway data plane can only join an AI Gateway. Each pipeline is served at `/pipelines/{slug}/chat/completions`, and the request body must name the pipeline's model as `"model": "gs-{slug}"`. AI Gateway has no default model, so a request without it fails with 503 (`name resolution failed`). The playground adds it for you, and the deploy response shows it.

| Service | Port | Notes |
|---|---|---|
| `kong-dp` | 18000 (proxy), 18100 (status) | Kong AI Gateway 2.1 (`kong/kong-ai-gateway:2.1`) data plane connected to the Konnect AI Gateway with a registered client certificate. |
| `guardrail-engine` | 18080 | Preloads Presidio (ja) and the sentiment model. Llama Guard uses the host's Ollama through `OLLAMA_HOST`. |
| `studio-api` | 18200 | Pipelines (SQLite volume), compiler, Konnect deploy, playground. The Konnect PAT comes from `~/.kong/kpat` via `scripts/up.sh`. |
| `web` | 13000 | The UI (nginx). Proxies `/api` to studio-api. |
| `ollama` | – | Optional (`--profile ollama`). Use it if there is no Ollama on the host. |

Always (re)start containers with `scripts/up.sh [service…]`, not plain `docker compose up`. Otherwise the secret variables are empty, and `studio-api` loses Konnect access.

The OpenAI key is passed to `kong-dp` as the environment variable `OPENAI_AUTH_HEADER` (`Bearer <key>`), which `{vault://env/OPENAI_AUTH_HEADER}` in the pipeline's model provider references. The DP certificate and key are passed the same way, as `KONG_CLUSTER_CERT` and `KONG_CLUSTER_CERT_KEY`. None of these are written to disk, but `docker inspect` shows them.

## Layout

| Path | What it is |
|---|---|
| `common/guardrail_common/` | Shared by all parts. `graph.py`: the pipeline graph (React Flow shape). `catalog.py`: node types with JSON Schemas. `conditions.py`: condition rules. |
| `engine/guardrail_engine/` | FastAPI service that runs custom detectors and control nodes. Kong DataKit calls it. |
| `studio-api/guardrail_studio/` | FastAPI backend. `compiler/` turns a graph into AI Gateway entities (model provider, model, policies), also as a kongctl-style declarative file. `konnect.py` deploys them. `store.py` keeps pipelines and versions. `main.py` serves the UI API. |
| `web/` | Vite + React + React Flow UI (`src/App.tsx`, `src/components/`). |
| `samples/` | Example pipelines (`jp-support.json`). |

## How a graph runs on Kong AI Gateway

A pipeline becomes three kinds of Konnect AI Gateway entities, all labelled `pipeline: {slug}`:
- **LLM** becomes a model provider `gs-{slug}-llm` and a model `gs-{slug}` that targets it. The model's route is `/pipelines/{slug}` (AI Gateway appends `/chat/completions`).
- **Native nodes** (`ai_prompt_guard`, `ai_sanitizer`, `ai_azure_content_safety`, …) each become a policy whose type is the Kong plugin name. The model lists the policies.
- **Custom and control nodes** (Llama Guard, Presidio PII, sentiment/kasuhara, Laya, keyword, condition, block) in one phase form a *segment*. One DataKit policy sends the segment spec plus the chat body to `POST /v1/segments/execute`. The engine returns either `allow` with the rewritten (masked) body, or `block` with a status. DataKit then writes the body upstream or exits with that status.

**Execution order is Kong's plugin priority, not the canvas.** AI Gateway policies don't accept `ordering`, and the order of the model's `policies` list has no effect. On the data plane, DataKit (the engine segment) runs before the native AI plugins even when a native node comes first on the canvas. Compiling warns about this whenever a phase has more than one policy.

A model runs one instance of each policy type. That gives two canvas rules, and `validate` checks both:
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
uv run pytest                    # real-model tests are skipped
uv run pytest -m models          # real models: needs `uv sync --extra models` (in engine/), spaCy ja_core_news_trf, Ollama
UPDATE_GOLDEN=1 uv run pytest studio-api/tests/test_compile.py   # regenerate the AI Gateway golden file after an intended change

uv run uvicorn guardrail_engine.main:app --port 8080      # engine
uv run uvicorn guardrail_studio.main:app --port 8000      # studio-api
```

Engine environment variables:
- `GUARDRAIL_PRELOAD=presidio_pii,sentiment_kasuhara`: load these models at startup.
- `PRESIDIO_LANGUAGES=ja,en`: languages Presidio loads.

studio-api environment variables:
- `GUARDRAIL_ENGINE_URL`: engine URL written into the DataKit `call` node.
- `STUDIO_CORS_ORIGINS`: allowed origins for the Web UI.

## Laya questions (typed triage)

The **Laya Classifier** node asks your own questions about the prompt in a single model pass. In the Inspector you edit `questions` as JSON text. **+ yes/no / + choice / + score** insert a template question, and **Reset to presets** restores the defaults.

| Type | Laya answer | `flags.<name>` | Counts as a hit (`detect_on`) when |
|---|---|---|---|
| `noul` | yes/no probability | `true` if ≥ `threshold` (default 0.75) | the flag is true |
| `choice` | one label from `criteria` (object) | the label | never; branch on the label |
| `score` | a level from `criteria` (list) | level number (0…n-1) | level ≥ `threshold` (default: top level) |

The presets are `pii` (noul), `intent` (choice), `urgency` (score) and `security_risk` (noul). Refer to the prompt as `` `message` `` in `instructions`. `threshold` is Guardrail Studio's own field and is stripped before the questions are sent to Laya. Probabilities go in `details.scores.<name>`.

Laya always **flags**: it never blocks on its own. Put a **Condition** node after it to decide what happens.

The condition's rule editor builds rules from the answers of the detectors that run before it:
- **Source:** only those upstream detectors.
- **Field:** the source's outputs; for Laya, one entry per question.
- **Operator and value:** match the field type. Yes/no questions get `is true/false`, choice questions get their labels (`is`, `is one of`), score questions get their levels (`≥ 2: urgent, blocking work`), and probabilities get a number.

Connecting a detector to an empty condition creates a first rule, such as `intent is question` for the Laya presets. Examples:
- `flags.intent eq "complaint"`
- `flags.urgency gte 2`
- `details.scores.pii gt 0.95`

Validation catches:
- malformed questions
- `detect_on` naming a missing or `choice` question
- a score threshold above the top level
- rules on a node that doesn't run before the condition
- rules on fields the source doesn't produce
- operators or values that don't fit the field (e.g. `detected gte 0.8`, since `detected` is true/false)

The same field list (`guardrail_common/outputs.py`) drives both the editor and the validator.

In tests with Japanese support prompts, `intent` and custom questions were accurate. `pii` and `security_risk` still flag harmless prompts even at 0.9, and `urgency` rated most prompts 1–2, even one with a 10-minute deadline. Use Presidio and Llama Guard for blocking, and Laya for routing.

## Llama Guard safety policy and task instruction

The **Llama Guard Safety** node's Inspector has two settings:
- **Task instruction:** the first line of the Llama Guard prompt. `{role}` becomes *User* for request checks and *Agent* for response checks.
- **Safety policy:** the unsafe content categories. You can switch each one on or off, rename it, give it a description, or add your own. **Preview prompt** shows the exact text sent to the model.

The engine builds the prompt itself and sends it to Ollama in raw mode (`guardrail_common/llamaguard.py`). With the default settings it matches the prompt from Ollama's `llama-guard3` template: in tests the answers were identical. Unlike that template, the final instruction names the right role for response checks.

How the policy is applied, based on tests with `llama-guard3:1b`:
- **Codes are fixed.** Standard categories keep S1–S14; custom categories get S15 and up. The 1B model answers with its trained codes whatever the prompt lists, so renumbering would mislabel its answers.
- **Disabling filters the answer.** A disabled category is ignored in Llama Guard's answer: e.g. turning off *Privacy* lets contact details through, while weapons prompts are still blocked. The trained categories S1–S13 are still listed in the prompt. Removing one from the prompt makes the 1B model pick another code (a PII prompt came back as S1 Violent Crimes).
- **Custom categories and task changes need a model that follows the prompt**, such as `llama-guard3:8b` (set `model` on the node). The 1B model ignored custom categories and descriptions in every test.

Condition rules can branch on `flags.categories contains <id>`; the editor lists the enabled categories.

## Changes from the guardrail_agent prototype

- The sentiment model default is now `koheiduck/bert-japanese-finetuned-sentiment`. `cl-tohoku/bert-base-japanese-v3` has no classification head, so its labels were random.
- Llama Guard calls the Ollama REST API directly and honors `fail_mode`.
- Presidio's language comes from node config.
- Laya questions are configurable (see above), and their answers are flags that condition nodes can branch on.

## Verified on Kong AI Gateway 2.1

- DataKit `output: service_request.body` rewrites the body before the model's proxy reads it (masked PII reaches the LLM as `<EMAIL_ADDRESS>`).
- `{vault://env/OPENAI_AUTH_HEADER}` in the model provider's auth header resolves on the data plane.
- Policies run in Kong plugin priority order, whatever the order of the model's `policies` list (DataKit before `ai-prompt-guard`).

Still to verify:
- One DataKit policy that carries both request- and response-phase nodes.
- Field names in the native plugin schemas (`catalog.py`) for AI Gateway 2.1.

The old Kong Gateway control plane named `guardrail-service` (`/v2/control-planes`) is no longer used. Delete it in Konnect when you no longer need it.
