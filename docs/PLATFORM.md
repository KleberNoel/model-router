# Agent Platform Runbook

## Target Architecture

```text
Open WebUI / phone ──OpenAI──▶ model-router ──▶ Hermes or model backend
Goose launcher ─────ACP──────▶ Goose ──OpenAI──▶ model-router
Streamlit ─────────REST──────▶ model-router harness API
Goose/Hermes ──────MCP───────▶ harness memory/TODO tools
Google Tasks ──────sync──────▶ canonical TODO records in PostgreSQL
```

`model-router` is the inference data plane. The harness API is the control
plane. Goose remains local because it needs filesystem and shell access. Do
not expose Goose ACP or a shell endpoint to the public internet.

Use `docker-compose.platform.yml` as an overlay on top of
`docker-compose.stack.yml`. This keeps the base stack compatible with the
existing deployment while adding the Postgres, Qwen, harness, worker, and
console settings without embedding credentials in Compose.

## Canonical Database

Use a private managed PostgreSQL instance for production. Keep Open WebUI in
its own database. The router database should contain auth, routes, usage,
profiles, runs, memories, TODOs, OAuth connections, and outbox events.

Set:

```text
MODEL_ROUTER_DATABASE_URL=postgresql+psycopg://...
MODEL_ROUTER_DATABASE_AUTO_CREATE=false
```

Create the schema before starting the router:

```bash
MODEL_ROUTER_DATABASE_URL='postgresql+psycopg://...' alembic upgrade head
```

Migrate the existing SQLite state once:

```bash
MODEL_ROUTER_SOURCE_DATABASE_URL='sqlite:////path/to/model_router.db' \
MODEL_ROUTER_DATABASE_URL='postgresql+psycopg://...' \
python scripts/migrate_sqlite_to_postgres.py
```

Take a database backup and stop writes before migration. The script does not
truncate the target and should not be run repeatedly against a populated
database.

## Local Goose

Create a `goose-coding` profile through the harness API or Streamlit, then:

```bash
export MODEL_ROUTER_API_KEY='scoped-agent-key'
python scripts/run_goose_profile.py --profile goose-coding
```

The launcher reads the profile, sets Goose's OpenAI-compatible provider to the
selected model-router route, and starts `goose acp` in the configured workspace.

## Google OAuth

Configure a Google Cloud OAuth web client with the exact callback URL:

```text
https://your-router-host/api/v1/integrations/google/callback
```

Set `MODEL_ROUTER_GOOGLE_OAUTH_ENCRYPTION_KEY` to a Fernet key stored in the
deployment secret manager. Each user connects their own Google account. The
database remains canonical; Google Tasks is a bidirectional projection.

Use Calendar for events and Drive for documents. Do not put Calendar event IDs
or Drive file IDs in the TODO table; add separate resource tables when those
adapters are enabled.

Google Calendar and Google Drive connectors are intentionally not enabled in
this first implementation. Add them as separate OAuth-scoped adapters after
Tasks sync is stable:

- Calendar adapter: event start/end, timezone, recurrence, attendees.
- Drive adapter: file ID, MIME type, parent folder, revision, and permission
  metadata; file contents should remain in Drive, not in the memory table.

## Gmail Draft Triage

The optional Gmail worker is deliberately constrained:

```text
Gmail important/unread mail
  -> read message
  -> search tenant/user memory
  -> draft only if context matches
  -> model-router reasoning route
  -> Gmail draft creation
```

It does not expose or call Gmail send, delete, archive, or label operations.
`gmail.compose` is required by Google for draft creation, so the OAuth token
has more capability than the application uses; keep the implementation
draft-only and require human review in Gmail.

Enable after OAuth setup:

```text
MODEL_ROUTER_GMAIL_TRIAGE_ENABLED=true
MODEL_ROUTER_GMAIL_TRIAGE_INTERVAL_SECONDS=900
MODEL_ROUTER_GMAIL_TRIAGE_MODEL_ROUTE=local/qwen-reasoning
```

Use `POST /api/v1/integrations/google/gmail/triage` for a manual dry run before
starting the `gmail-triage-worker` profile. Gmail body text is untrusted input
and is explicitly separated from approved memory context in the drafting
prompt.

## Scale Boundary

The current local GPU manager is single-process and exclusive. Multiple GPU
workers require a scheduler with leases, queue depth, worker health, and
route-to-worker capacity metadata. Do not advertise one local route as
1000-user capacity. PostgreSQL plus a Redis-backed queue and multiple
llama-server workers are the next scale step.

For 10-1000 users, add:

- managed Postgres with point-in-time recovery
- Redis or a managed queue for run dispatch and rate limits
- separate worker processes for Goose/Hermes/model execution
- per-tenant quotas and concurrency limits
- OIDC/Google OAuth, invitations, audit logs, and key rotation
- OpenTelemetry collector plus a self-hosted trace/metrics backend

## Current Qwen Local Route

The active Qwen artifact is:

```text
Qwen3.6-35B-A3B-Claude-4.7-Opus-Reasoning-Distilled.Q4_K_M.gguf
```

Its GGUF metadata reports:

- `qwen35moe` architecture
- 40 layers
- 256 experts, 8 active experts
- native context: `262144`
- embedded Qwen chat template
- `Q4_K_M` quantization

The managed route uses the turboquant server with:

```text
-ngl 20
-c 262144
-np 1
-fa 1
--cache-type-k turbo4
--cache-type-v turbo3
```

The 262k context starts successfully on the RTX 3090 by keeping part of the
model and KV state in host RAM. A warm benchmark measured approximately:

- prompt processing: approximately `105 tokens/s` with full GPU offload
- generation: approximately `143 tokens/s` with full GPU offload

The earlier partial-offload setting (`-ngl 20`) produced only `13-14 tokens/s`
for generation because many layers were running from host RAM. The current
managed route uses full offload (`-ngl all`), which fits this Q4 artifact and
is substantially faster. These are single-request measurements, not a
capacity guarantee. Long prompts will reduce prompt throughput and concurrent
requests are intentionally limited by the single-slot configuration.

## Next Engineering Steps

1. **Postgres cutover**
   - Choose the managed provider and EU region.
   - Provision a dedicated `model_router` database and role.
   - Run `alembic upgrade head` with `MODEL_ROUTER_DATABASE_AUTO_CREATE=false`.
   - Stop writes, back up SQLite, run `scripts/migrate_sqlite_to_postgres.py`, and verify row counts.
   - Repoint router, workers, console, and sync jobs to the same Postgres URL.

2. **Connection and concurrency hardening**
   - Add PgBouncer or provider pooler.
   - Add Redis for run queues, rate limits, and distributed locks.
   - Replace polling workers with leased queue claims.
   - Add per-tenant request, token, and concurrent-run quotas.

3. **Maintainability**
   - Replace `create_all` legacy compatibility with versioned Alembic revisions.
   - Add API integration tests using a temporary Postgres service.
   - Add contract tests for OpenAI, Hermes, Goose launcher, MCP, and Google sync.
   - Add route capability validation so clients cannot select unsupported tools/context.
   - Move long Compose environment values into mounted config files or a secret manager.

4. **Observability**
   - Deploy an OpenTelemetry Collector.
   - Export traces, model timings, queue wait, route switches, and sync failures.
   - Retain prompts/content only with an explicit privacy policy; default telemetry should contain metadata.
   - Add dashboards for tokens, latency, GPU load, OOMs, failed jobs, and Google sync lag.

5. **Landscope integration**
   - Keep Landscope PostGIS tables separate from router tables, even if both use the same Postgres cluster.
   - Give the validation worker a narrow database role or service API.
   - Send enriched facts to Qwen and persist structured validation output separately from deterministic scores.
   - Add idempotency keys and job leases around `refresh_*`, `revalidate`, and report generation.

6. **Production security**
   - Rotate all keys that appeared in shell files or chat history.
   - Use Google OAuth/OIDC for people and scoped service keys for OpenAI-compatible clients.
   - Put the router and console behind HTTPS, authentication, and a private network or VPN.
   - Never expose Goose ACP, ttyd, Docker socket, or unrestricted shell access publicly.
