#!/usr/bin/env python3
"""Launch Goose ACP from a harness profile.

The API key is read from MODEL_ROUTER_API_KEY and is never printed. This is a
local launcher by design: Goose owns the local ACP process and filesystem
permissions, while the harness owns profile policy and model selection.
"""

import argparse
import os
import subprocess
from pathlib import Path

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="goose-coding")
    parser.add_argument("--router-url", default=os.getenv("MODEL_ROUTER_URL", "http://127.0.0.1:4000"))
    parser.add_argument("--goose", default="goose")
    args = parser.parse_args()

    api_key = os.environ.get("MODEL_ROUTER_API_KEY")
    if not api_key:
        raise SystemExit("MODEL_ROUTER_API_KEY is required")

    response = httpx.get(
        f"{args.router_url.rstrip('/')}/api/v1/profiles",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    response.raise_for_status()
    profile = next((item for item in response.json() if item["name"] == args.profile), None)
    if profile is None:
        raise SystemExit(f"Profile not found: {args.profile}")
    if profile["runtime"] != "goose":
        raise SystemExit(f"Profile {args.profile} is configured for {profile['runtime']}, not goose")
    if not profile.get("workspace"):
        raise SystemExit(f"Profile {args.profile} has no workspace")

    workspace = Path(profile["workspace"]).expanduser().resolve()
    if not workspace.is_dir():
        raise SystemExit(f"Workspace does not exist: {workspace}")

    environment = {
        **os.environ,
        "GOOSE_PROVIDER": "openai",
        "GOOSE_MODEL": profile["model_route"],
        "OPENAI_BASE_URL": f"{args.router_url.rstrip('/')}/v1",
        "OPENAI_API_KEY": api_key,
    }
    subprocess.run(
        [args.goose, "acp", "--with-builtin", "developer,analyze,skills,todo"],
        cwd=workspace,
        env=environment,
        check=True,
    )


if __name__ == "__main__":
    main()
