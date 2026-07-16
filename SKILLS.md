# Project Skills

Operational guidance for agents and contributors working in this repository.

## Architecture

- `model-router` is the OpenAI-compatible inference gateway and authentication boundary.
- Goose runs locally through ACP because it needs filesystem and shell access.
- Hermes is a separate agent runtime reached through an OpenAI-compatible route.
- Open WebUI is a client, not the canonical application database.
- Streamlit is an operations console and must use the harness API rather than write directly to the database.
- Persistent profiles, runs, memory, TODOs, OAuth connections, and outbox events belong in the router/harness database.
- Landscope tables remain separate from router tables, even when both use one PostgreSQL cluster.

## Safety

- Never commit `.env`, `.env.stack`, API keys, OAuth secrets, private keys, tokens, or model artifacts.
- Use environment interpolation such as `{env:MODEL_ROUTER_API_KEY}` in client templates.
- Do not print credentials in logs, command output, commit messages, or documentation.
- Do not expose Goose ACP, ttyd, the Docker socket, or unrestricted shell access to the public internet.
- Prefer Tailscale Serve for private sharing; use Funnel only after adding strong authentication and rate limits.
- Do not rotate or revoke user credentials without explicit approval.
- Never use destructive git commands or overwrite unrelated worktree changes.

## Model Operations

- Managed local routes use `upstream_base_url=managed://llama-server`.
- Only one managed model process may use the GPU at a time; busy model switches return `503`.
- Validate GGUF metadata and integrity before adding a route.
- Keep model capability metadata accurate, especially `tools`, `reasoning`, context, and streaming support.
- Benchmark through `model-router`, not only a standalone llama-server process.
- Keep model files outside git under the configured models directory.

## Database

- SQLite is acceptable for local development only.
- Production uses managed PostgreSQL with `MODEL_ROUTER_DATABASE_AUTO_CREATE=false`.
- Apply schema changes through Alembic revisions.
- Back up before SQLite-to-Postgres migration and verify row counts afterward.
- Use separate database roles for router, Landscope, workers, and read-only reporting where practical.
- Do not put Google Calendar or Drive records directly into the TODO table; use dedicated adapter tables.

## Verification

Run before committing:

```bash
pytest -q
python -m compileall -q app console scripts migrations
docker compose --env-file .env.stack -f docker-compose.stack.yml config --quiet
git diff --check
```

Check the live service without exposing secrets:

```bash
curl -fsS http://127.0.0.1:4000/healthz
curl -fsS http://127.0.0.1:4000/readyz
curl -fsS http://127.0.0.1:4000/status
```

## Deployment

- Use `docs/PLATFORM.md` for the Postgres cutover, Google OAuth, worker, and scale plan.
- Keep production secrets in a deployment secret manager or protected environment.
- Use scoped service API keys for OpenCode, Open WebUI, the runner, and the console.
- Rotate keys that have appeared in shell files or chat history during the next approved maintenance window.
