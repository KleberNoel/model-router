#!/usr/bin/env python3
"""Model-router startup: health-check routes + TTL-clean web search data.

1. Mark route active/inactive based on upstream reachability.
2. Purge web-search-* documents older than 30 days from Open WebUI's pgvector.
3. Optionally discover running llama-server containers and register them.

Run inside the model-router container or with network access to both services.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone


# ── helpers ──────────────────────────────────────────────────────────


def _json_get(url: str, headers: dict | None = None, retries: int = 10) -> dict:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers or {})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3)


def _json_post(url: str, body: dict, headers: dict | None = None, retries: int = 3, timeout: int = 30) -> dict:
    for attempt in range(retries):
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(url, data=data, headers=headers or {})
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(3)


# ── route health check ───────────────────────────────────────────────


def health_check_routes(router_url: str, api_key: str) -> dict[str, bool]:
    """Verify model-router is up and models are listed. Returns {name: True}."""
    models = _json_get(f"{router_url}/v1/models", {"Authorization": f"Bearer {api_key}"})
    results: dict[str, bool] = {}
    for m in models.get("data", []):
        name = m["id"]
        results[name] = True
    return results


# ── pgvector TTL cleanup ─────────────────────────────────────────────


def purge_web_search_collections(pg_dsn: str, ttl_days: int = 30) -> int:
    """Delete web-search-* collections older than *ttl_days* from pgvector.

    Uses psycopg (v3) which is already in the model-router image.
    """
    import psycopg

    conn = psycopg.connect(pg_dsn, autocommit=True)
    cur = conn.cursor()

    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=ttl_days)).timestamp())

    cur.execute(
        "DELETE FROM document_chunk "
        "WHERE collection_name IN ("
        "  SELECT collection_name FROM document "
        "  WHERE collection_name LIKE %s AND timestamp < %s"
        ")",
        ("web-search-%", cutoff_ts),
    )
    chunks_deleted = cur.rowcount

    cur.execute(
        "DELETE FROM document WHERE collection_name LIKE %s AND timestamp < %s",
        ("web-search-%", cutoff_ts),
    )
    docs_deleted = cur.rowcount

    cur.close()
    conn.close()
    return chunks_deleted + docs_deleted


# ── optional: Docker container discovery ─────────────────────────────


def discover_llama_containers(router_db_path: str, router_base_url: str) -> list[str]:
    """Scan Docker for running llama-server containers, register as routes.

    Requires docker CLI and access to the socket.
    """
    import sqlite3
    import subprocess
    import uuid

    try:
        out = subprocess.check_output(
            [
                "docker", "ps", "--filter", "ancestor=vito974/llama-cpp-turboquant:server-cuda12",
                "--format", "{{.ID}}\t{{.Image}}\t{{.Names}}\t{{.Ports}}",
            ],
            text=True,
            timeout=10,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []

    discovered = []
    for line in out.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        cid, image, name, ports = parts[0], parts[1], parts[2], parts[3] if len(parts) > 3 else ""

        # Extract host port
        host_port = "8080"
        for part in ports.split(","):
            part = part.strip()
            if "->8080" in part:
                host_port = part.split(":")[-1].split("-")[0]
                break

        # Get the model name from the container args
        try:
            info = subprocess.check_output(
                ["docker", "inspect", cid, "--format", "{{.Args}}"],
                text=True, timeout=5,
            )
            import re
            model_match = re.search(r'-m\s+(/\S+\.gguf)', info)
            if model_match:
                model_path = model_match.group(1)
                model_name = os.path.basename(model_path).replace(".gguf", "")
            else:
                model_name = name
        except Exception:
            model_name = name

        route_name = model_name.replace("_", "-").replace(".", "-")

        # Register in model-router DB
        try:
            conn = sqlite3.connect(router_db_path)
            existing = conn.execute(
                "SELECT id FROM model_routes WHERE name = ?", (route_name,)
            ).fetchone()
            if not existing:
                # Find default tenant
                tenant = conn.execute(
                    "SELECT id FROM tenants WHERE slug = 'default'"
                ).fetchone()
                tid_list = json.dumps([tenant[0]]) if tenant else "[]"
                conn.execute(
                    """INSERT INTO model_routes
                       (id, name, upstream_base_url, upstream_model_name,
                        allowed_tenant_ids, is_active, max_context_tokens, upstream_headers)
                       VALUES (?, ?, ?, ?, ?, 1, NULL, '{}')""",
                    (
                        str(uuid.uuid4()),
                        route_name,
                        f"{router_base_url}/v1" if "managed" not in router_base_url else router_base_url,
                        model_name,
                        tid_list,
                    ),
                )
                conn.commit()
                discovered.append(route_name)
            conn.close()
        except Exception as e:
            print(f"  DB error for {route_name}: {e}", file=sys.stderr)

    return discovered


# ── main ─────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Model-router startup checks")
    parser.add_argument("--router-url", default="http://model-router:4000")
    parser.add_argument("--api-key", default=os.getenv("MODEL_ROUTER_API_KEY", ""))
    parser.add_argument("--pg-dsn", default=os.getenv("PG_DSN",
        "postgresql://openwebui:openwebui@db:5432/openwebui"))
    parser.add_argument("--ttl-days", type=int, default=30)
    parser.add_argument("--skip-ttl", action="store_true")
    parser.add_argument("--skip-health", action="store_true")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--router-db", default="/data/model_router.db")
    args = parser.parse_args()

    # 1. Health check routes
    if not args.skip_health and args.api_key:
        print("=== Route health check ===")
        results = health_check_routes(args.router_url, args.api_key)
        for name, ok in results.items():
            status = "✓" if ok else "✗"
            print(f"  {status} {name}")

    # 2. TTL cleanup
    if not args.skip_ttl:
        print(f"\n=== Web search TTL cleanup ({args.ttl_days}d) ===")
        try:
            deleted = purge_web_search_collections(args.pg_dsn, args.ttl_days)
            print(f"  Purged {deleted} rows from document/document_chunk")
        except Exception as e:
            print(f"  TTL cleanup failed: {e}", file=sys.stderr)

    # 3. Discover llama containers
    if args.discover:
        print("\n=== Container discovery ===")
        found = discover_llama_containers(args.router_db, args.router_url)
        if found:
            for name in found:
                print(f"  + {name}")
        else:
            print("  No llama containers found")


if __name__ == "__main__":
    main()
