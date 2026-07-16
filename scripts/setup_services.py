#!/usr/bin/env python3
"""Configure model-router secrets and database mode without printing secrets."""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path


def parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key] = value
    return result


def write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text().splitlines() if path.exists() else []
    output: list[str] = []
    written: set[str] = set()
    for line in existing:
        stripped = line.strip()
        key = stripped.split("=", 1)[0] if stripped and not stripped.startswith("#") and "=" in stripped else None
        if key in values:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output).rstrip() + "\n")
    path.chmod(0o600)


def is_placeholder(value: str) -> bool:
    value = value.lower()
    return not value or value.startswith(("replace-with-", "generate-a-")) or "_replace_with_" in value


def generate_api_key() -> str:
    return f"mrk_{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"


def generate_fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def get_value(
    label: str,
    *,
    current: str = "",
    default: str = "",
    secret: bool = False,
    required: bool = False,
    non_interactive: bool = False,
) -> str:
    if current and not is_placeholder(current):
        return current
    if non_interactive:
        if required and not default:
            raise SystemExit(f"Missing required value: {label}")
        return default
    prompt = f"{label}{f' [{default}]' if default else ''}: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip() or default
    if required and not value:
        raise SystemExit(f"Missing required value: {label}")
    return value


def bootstrap_routes(deepseek_key: str) -> str:
    routes = [
        {
            "name": "hermes-agent",
            "description": "Hermes API server",
            "upstream_base_url": "http://hermes:8642/v1",
            "upstream_model_name": "hermes-agent",
            "allowed_tenant_slugs": ["default"],
        },
        {
            "name": "functiongemma-270m-it-Q4_K_M.gguf",
            "description": "FunctionGemma tool-calling model",
            "upstream_base_url": "managed://llama-server",
            "upstream_model_name": "functiongemma-270m-it-Q4_K_M.gguf",
            "allowed_tenant_slugs": ["default"],
            "max_context_tokens": 32768,
            "capabilities": {"streaming": True, "tools": True, "local": True},
        },
        {
            "name": "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf",
            "description": "Gemma 4 local model",
            "upstream_base_url": "managed://llama-server",
            "upstream_model_name": "gemma-4-31B-it-qat-UD-Q4_K_XL.gguf",
            "allowed_tenant_slugs": ["default"],
            "max_context_tokens": 192000,
        },
        {
            "name": "local/qwen-reasoning",
            "description": "Qwen3.6 Q4 reasoning model",
            "upstream_base_url": "managed://llama-server",
            "upstream_model_name": "local-qwen-reasoning",
            "allowed_tenant_slugs": ["default"],
            "max_context_tokens": 262144,
            "capabilities": {"streaming": True, "reasoning": True, "tools": True, "local": True, "kv_cache": "turbo4/turbo3"},
        },
    ]
    if deepseek_key:
        routes.append({
            "name": "deepseek-flash",
            "description": "DeepSeek Flash",
            "upstream_base_url": "https://api.deepseek.com/v1",
            "upstream_model_name": "deepseek-chat",
            "upstream_headers": {"Authorization": f"Bearer {deepseek_key}"},
            "allowed_tenant_slugs": ["default"],
        })
    return json.dumps(routes, separators=(",", ":"))


def compose_command(args: list[str], env_file: Path) -> list[str]:
    return ["docker", "compose", "--env-file", str(env_file), "-f", "docker-compose.stack.yml", "-f", "docker-compose.platform.yml", *args]


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure model-router secrets and database mode")
    parser.add_argument("--env-file", type=Path, default=Path(".env.stack"))
    parser.add_argument("--opencode-env-file", type=Path, default=Path("runtime/opencode.env"))
    parser.add_argument("--database", choices=("sqlite", "local-postgres", "postgres"), default="sqlite")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--migrate", action="store_true", help="Apply Alembic schema before a Postgres migration")
    parser.add_argument("--start", action="store_true", help="Start model-router after configuration")
    args = parser.parse_args()

    if not args.env_file.exists():
        template = Path(".env.stack.example")
        if not template.exists():
            raise SystemExit(".env.stack.example is missing")
        args.env_file.write_text(template.read_text())
    values = parse_env(args.env_file)
    non_interactive = args.non_interactive

    deepseek = get_value("DeepSeek API key (blank disables route)", current=os.getenv("DEEPSEEK_API_KEY", values.get("DEEPSEEK_API_KEY", "")), secret=True, non_interactive=non_interactive)
    google_id = get_value("Google OAuth client ID", current=os.getenv("MODEL_ROUTER_GOOGLE_OAUTH_CLIENT_ID", values.get("MODEL_ROUTER_GOOGLE_OAUTH_CLIENT_ID", "")), non_interactive=non_interactive)
    google_secret = get_value("Google OAuth client secret", current=os.getenv("MODEL_ROUTER_GOOGLE_OAUTH_CLIENT_SECRET", values.get("MODEL_ROUTER_GOOGLE_OAUTH_CLIENT_SECRET", "")), secret=True, non_interactive=non_interactive)
    redirect = get_value("Google OAuth redirect URI", current=os.getenv("MODEL_ROUTER_GOOGLE_OAUTH_REDIRECT_URI", values.get("MODEL_ROUTER_GOOGLE_OAUTH_REDIRECT_URI", "")), default="http://127.0.0.1:4000/api/v1/integrations/google/callback", non_interactive=non_interactive)
    fernet = get_value("Google credential encryption key", current=os.getenv("MODEL_ROUTER_GOOGLE_OAUTH_ENCRYPTION_KEY", values.get("MODEL_ROUTER_GOOGLE_OAUTH_ENCRYPTION_KEY", "")), secret=True, default=generate_fernet_key(), non_interactive=non_interactive)

    service_names = [
        "OPEN_WEBUI_ROUTER_API_KEY",
        "AGENT_CONSOLE_API_KEY",
        "MODEL_ROUTER_RUNNER_API_KEY",
        "MODEL_ROUTER_AGENT_RUNNER_API_KEY",
        "OPENCODE_MODEL_ROUTER_API_KEY",
    ]
    service_keys = {name: values.get(name, "") for name in service_names}
    for name in service_names:
        if is_placeholder(service_keys[name]):
            service_keys[name] = generate_api_key()

    bootstrap_keys = [
        {"tenant_slug": "default", "name": "Open WebUI service", "raw_key": service_keys["OPEN_WEBUI_ROUTER_API_KEY"], "scopes": ["models:read", "chat:completions"]},
        {"tenant_slug": "default", "name": "Agent console", "raw_key": service_keys["AGENT_CONSOLE_API_KEY"], "scopes": ["models:read", "agent:read", "agent:write", "memory:read", "memory:write", "todos:read", "todos:write", "integrations:google"]},
        {"tenant_slug": "default", "name": "Agent runner", "raw_key": service_keys["MODEL_ROUTER_RUNNER_API_KEY"], "scopes": ["models:read", "chat:completions"]},
        {"tenant_slug": "default", "name": "Gmail draft runner", "raw_key": service_keys["MODEL_ROUTER_AGENT_RUNNER_API_KEY"], "scopes": ["models:read", "chat:completions"]},
        {"tenant_slug": "default", "name": "OpenCode local client", "raw_key": service_keys["OPENCODE_MODEL_ROUTER_API_KEY"], "scopes": ["models:read", "chat:completions"]},
    ]

    database_url = {
        "sqlite": "sqlite:////data/model_router.db",
        "local-postgres": "postgresql+psycopg://openwebui:openwebui@db:5432/model_router",
    }.get(args.database)
    if args.database == "postgres":
        database_url = get_value("Managed PostgreSQL URL", current=os.getenv("MODEL_ROUTER_DATABASE_URL", ""), secret=True, required=True, non_interactive=non_interactive)

    values.update({
        "MODEL_ROUTER_DATABASE_URL": database_url or "",
        "MODEL_ROUTER_DATABASE_AUTO_CREATE": "true" if args.database == "sqlite" else "false",
        "MODEL_ROUTER_BOOTSTRAP_API_KEYS_JSON": json.dumps(bootstrap_keys, separators=(",", ":")),
        "MODEL_ROUTER_BOOTSTRAP_ROUTES_JSON": bootstrap_routes(deepseek),
        "DEEPSEEK_API_KEY": deepseek,
        "MODEL_ROUTER_GOOGLE_OAUTH_CLIENT_ID": google_id,
        "MODEL_ROUTER_GOOGLE_OAUTH_CLIENT_SECRET": google_secret,
        "MODEL_ROUTER_GOOGLE_OAUTH_REDIRECT_URI": redirect,
        "MODEL_ROUTER_GOOGLE_OAUTH_ENCRYPTION_KEY": fernet,
        **service_keys,
    })
    write_env(args.env_file, values)
    write_env(args.opencode_env_file, {
        "MODEL_ROUTER_API_KEY": service_keys["OPENCODE_MODEL_ROUTER_API_KEY"],
        "OPENAI_API_KEY": service_keys["OPENCODE_MODEL_ROUTER_API_KEY"],
        "OPENAI_BASE_URL": "http://127.0.0.1:4000/v1",
        "MODEL_ROUTER_URL": "http://127.0.0.1:4000",
    })
    print(f"Wrote protected configuration: {args.env_file}", file=sys.stderr)
    print(f"Wrote protected OpenCode environment: {args.opencode_env_file}", file=sys.stderr)
    print(f"Database mode: {args.database}", file=sys.stderr)
    print(f"DeepSeek route: {'enabled' if deepseek else 'disabled'}", file=sys.stderr)
    print(f"Google OAuth: {'configured' if google_id and google_secret else 'not configured'}", file=sys.stderr)

    if args.database == "local-postgres":
        subprocess.run(compose_command(["up", "-d", "db"], args.env_file), check=True)
        subprocess.run(compose_command(["exec", "-T", "db", "psql", "-U", "openwebui", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", "SELECT 'CREATE DATABASE model_router' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'model_router')\\gexec"], args.env_file), check=True)
    if args.migrate:
        if args.database == "sqlite":
            raise SystemExit("--migrate requires --database local-postgres or --database postgres")
        subprocess.run(compose_command(["run", "--rm", "model-router", "alembic", "upgrade", "head"], args.env_file), check=True)
        print("Stop writes, back up SQLite, then run scripts/migrate_sqlite_to_postgres.py with both URLs.", file=sys.stderr)
    if args.start:
        subprocess.run(compose_command(["up", "-d", "model-router"], args.env_file), check=True)


if __name__ == "__main__":
    main()
