# Model Router

Multi-tenant FastAPI router for local OpenAI-compatible inference backends.

This project is designed for a single-node deployment with separated services from day one:

- local inference backend, e.g. `llama-server` on `127.0.0.1:8080`
- FastAPI router on `:4000`
- Open WebUI on `:3000`
- Postgres or SQLite for router state
- optional Redis for future rate limits and revocation

The router can also manage a local `llama-server` process itself for routes configured with
`upstream_base_url=managed://llama-server`.

## Current scope

- browser login for all users via email and password
- short-lived JWT access tokens
- rotating refresh token cookie
- true multi-tenant memberships
- tenant-scoped API keys for OpenCode, Hermes, and other machine clients
- OpenAI-compatible proxy surface for `GET /v1/models`, `POST /v1/chat/completions`, and `POST /v1/completions`
- usage logging only, not enforcement, for limits in the first phase

## Best-practice client split

- Browsers and UIs: JWT access token plus refresh cookie
- OpenCode and Hermes: API keys

Why:

- browser clients benefit from short-lived sessions and refresh rotation
- machine clients need stable credentials that are easy to rotate without a refresh flow

The router accepts both JWT bearer tokens and API keys through the same `Authorization: Bearer ...` header. It also accepts `X-API-Key`.

## Project layout

```text
app/
  routers/
  services/
  config.py
  database.py
  deps.py
  main.py
  models.py
  schemas.py
  security.py
```

## Quick start

1. Create and activate an environment.
2. Install dependencies.

```bash
pip install -e .
```

3. Copy `.env.example` to `.env` and update secrets.

4. Run the app.

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 4000
```

5. On first startup, the router can bootstrap:

- one platform admin user
- one default tenant
- one default model route

using the `MODEL_ROUTER_BOOTSTRAP_*` and `MODEL_ROUTER_DEFAULT_*` environment variables.

## Verified local deployment

The working setup on this host is:

- `llama-server` running directly on the host at `http://127.0.0.1:8080`
- the FastAPI router running directly on the host at `http://127.0.0.1:4000`
- Open WebUI running in Docker with `network_mode: host`

## Managed llama-server mode

For cheap local serving with one hot model at a time, `model-router` can now start and stop
`llama-server` on demand.

What it does:

- starts `llama-server` when a request hits a managed route
- keeps the current model hot while requests are in flight
- unloads the model after an idle timeout
- switches to a different model only when the current managed server is idle

Current scope:

- exactly one managed `llama-server` process at a time
- model definitions live in `MODEL_ROUTER_MANAGED_LLAMA_MODELS_JSON`
- each managed route still lives in the database, but its `upstream_base_url` must be `managed://llama-server`

Example route:

- `name`: `qwen-35b`
- `upstream_base_url`: `managed://llama-server`
- `upstream_model_name`: `qwen-35b`

Example env:

```bash
MODEL_ROUTER_MANAGED_LLAMA_COMMAND=llama-server
MODEL_ROUTER_MANAGED_LLAMA_PORT=8090
MODEL_ROUTER_MANAGED_LLAMA_IDLE_TIMEOUT_SECONDS=900
MODEL_ROUTER_MANAGED_LLAMA_MODELS_JSON={"qwen-35b":{"model_path":"/models/qwen-35b.gguf","cli_args":["--ctx-size","16384","--n-gpu-layers","999"]}}
```

Notes:

- `llama-server` must be installed and available on `PATH`
- `cli_args` may not include `-m`, `--model`, `--host`, `--port`, or `--alias`; the router sets those
- if a different model is requested while the managed server is busy, the router returns `503` instead of killing an in-flight request

Open WebUI should be configured with:

- `OPENAI_API_BASE_URL=http://127.0.0.1:4000/v1`
- `OPENAI_API_KEY=<tenant API key from the router>`
- `OLLAMA_BASE_URL=http://127.0.0.1:9`

Why host networking for Open WebUI:

- the bridge-network container on this machine could not reliably reach the host router via `host.docker.internal:4000`
- `network_mode: host` removed that routing issue and restored model discovery

## Service separation

Recommended ports:

- inference: `127.0.0.1:8080`
- router: `0.0.0.0:4000`
- Open WebUI: `0.0.0.0:3000`

Only expose the router and UI publicly. Keep inference private.

## Core endpoints

Auth:

- `POST /auth/login`
- `POST /auth/refresh`
- `POST /auth/logout`
- `GET /auth/me`
- `GET /auth/tenants`
- `POST /auth/switch-tenant`

Admin:

- `POST /admin/tenants`
- `GET /admin/tenants`
- `POST /admin/users`
- `POST /admin/memberships`
- `POST /admin/model-routes`
- `GET /admin/model-routes`
- `POST /admin/api-keys`

OpenAI-compatible:

- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/completions`

## Current limitations

- no Alembic migrations yet, tables are created with SQLAlchemy metadata
- no hard quota enforcement yet, usage is only logged
- no Redis-backed revocation or rate limiting yet
- no Open WebUI-specific SSO flow yet; Open WebUI should use a service API key in phase 1
- `docker-compose.yml` is intentionally limited to the verified Open WebUI container path; the router and inference server are currently host-run services in the tested setup
- managed `llama-server` mode currently runs a single model process at a time

## Full stack compose

For a containerized stack that keeps `model-router` as the required front door, use:

- `docker-compose.stack.yml` for `model-router` + Hermes + Open WebUI
- the optional `llama-cpp` sidecar via `--profile llama`

Example:

```bash
cp .env.stack.example .env.stack
docker compose --env-file .env.stack -f docker-compose.stack.yml up -d
docker compose --env-file .env.stack -f docker-compose.stack.yml --profile llama up -d
```

The stack uses two bootstrap env vars on first start:

- `MODEL_ROUTER_BOOTSTRAP_ROUTES_JSON` to seed routed model backends such as `hermes-agent` and `gemma-4-31B-it-qat-UD-Q4_K_XL.gguf`
- `MODEL_ROUTER_BOOTSTRAP_API_KEYS_JSON` to seed stable tenant API keys such as the Open WebUI service key

The optional `llama-cpp` compose profile now defaults to loading:

- `unsloth/gemma-4-31B-it-qat-GGUF:UD-Q4_K_XL` via `llama-server -hf`

Notes for this path:

- model downloads are cached under `LLAMA_CACHE_DIR`
- the sidecar still expects a host `llama-server` binary mounted through `LLAMA_CPP_BIN_DIR`
- `LLAMA_SPEC_TYPE=none` is the safe default on this host
- enabling Gemma MTP currently depends on a `llama.cpp` build that can load the Unsloth drafter without the `gemma4-assistant` architecture error we observed locally

If your `llama.cpp` build supports the drafter cleanly, set:

```bash
LLAMA_SPEC_TYPE=draft-mtp
LLAMA_HF_DRAFT_REPO=unsloth/gemma-4-31B-it-qat-GGUF
```

Otherwise keep speculative decoding disabled in compose until the binary is updated.

In Open WebUI terms, a Hermes-to-Open-WebUI tool-sharing layer should be treated as a `tool server`.
A small translation service that exposes Hermes tools to Open WebUI is best described here as a `tool bridge` or `tool adapter`.

## OpenCode integration

Point OpenCode at the model-router as an OpenAI-compatible provider.

### Config (`~/.config/opencode/opencode.json`)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "model": "model-router/gemma-4-31B-it-qat-UD-Q4_K_XL.gguf",
  "provider": {
    "model-router": {
      "name": "Local Model Router",
      "api": "openai",
      "options": {
        "baseURL": "http://127.0.0.1:4000/v1",
        "apiKey": "<your-tenant-api-key>"
      },
      "models": {
        "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf": {
          "id": "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf",
          "name": "Gemma 4 31B",
          "family": "gemma-4",
          "release_date": "2025-05-01",
          "attachment": false,
          "reasoning": true,
          "temperature": true,
          "tool_call": true,
          "interleaved": { "field": "reasoning_content" },
          "cost": { "input": 0, "output": 0 },
          "limit": { "context": 192000, "output": 8192 },
          "options": {},
          "modalities": { "input": ["text"], "output": ["text"] }
        }
      }
    }
  }
}
```

The `cost` is zero because this is a local model. OpenCode displays token usage
and context fill percentage using values returned by the router.

### What displays in OpenCode

| Display               | Source                          |
|-----------------------|---------------------------------|
| `102.0K`  (tokens)    | `usage.total_tokens` from API   |
| `(10%)`   (context %) | `total_tokens / limit.context`  |
| `$0.04`   (cost)      | `cost.input` × `cost.output`    |

Token usage is persisted in `usage_logs` in the router database for audit.

### Versioning

- **This repo**: infrastructure (docker-compose, model-router source, env templates).
- **Dotfiles** (`~/.config/opencode/opencode.json`): personal client config — keep in your dotfiles repo.

## Working hardware config

Tested on NVIDIA GeForce RTX 3090 (24 GB VRAM):

| Parameter            | Value                                                   |
|----------------------|---------------------------------------------------------|
| Model                | `unsloth/gemma-4-31B-it-qat-GGUF:UD-Q4_K_XL` (~16.5 GB) |
| Context window       | 192,000 tokens                                          |
| KV cache             | ~4.3 GB (turboquant: `turbo4` K, `turbo3` V)           |
| Total VRAM           | ~21.4 GB / 24 GB                                        |
| Max stable context   | 192k (256k OOMs on 24 GB)                               |

Turboquant compressed KV cache types are required to fit 192k context in 24 GB.
Without them, even 128k exceeds VRAM.
