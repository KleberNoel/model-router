import asyncio
import json
import subprocess
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Awaitable, Callable

import httpx
from fastapi import HTTPException, status

from app.config import Settings, get_settings
from app.models import ModelRoute

MANAGED_LLAMA_UPSTREAM = "managed://llama-server"
_RESERVED_LLAMA_ARGS = {"-m", "--model", "--host", "--port", "--alias"}


async def _noop_release() -> None:
    return None


@dataclass(slots=True)
class UpstreamLease:
    upstream_base_url: str
    _release: Callable[[], Awaitable[None]]
    _released: bool = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        await self._release()


@dataclass(slots=True)
class ManagedLlamaModel:
    model_path: str
    cli_args: list[str]
    idle_timeout_seconds: int | None = None
    startup_timeout_seconds: int | None = None


class LlamaServerManager:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._lock: asyncio.Lock | None = None
        self._managed_models: dict[str, ManagedLlamaModel] | None = None
        self._process: subprocess.Popen | None = None
        self._current_route_name: str | None = None
        self._current_upstream_base_url: str | None = None
        self._current_idle_timeout_seconds: int | None = None
        self._active_requests = 0
        self._last_used_monotonic = 0.0
        self._generation = 0
        self._idle_task: asyncio.Task | None = None

    async def acquire(self, route: ModelRoute) -> UpstreamLease:
        if route.upstream_base_url != MANAGED_LLAMA_UPSTREAM:
            return UpstreamLease(upstream_base_url=route.upstream_base_url, _release=_noop_release)

        config = self._managed_model_for_route(route)
        lock = self._get_lock()
        async with lock:
            self._cancel_idle_shutdown_locked()

            if self._process is not None and self._process.poll() is not None:
                await self._stop_current_process_locked()

            if self._process is not None and self._current_route_name != route.name:
                if self._active_requests > 0:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=(
                            "Managed llama-server is busy with "
                            f"'{self._current_route_name}'. Retry after the active request finishes."
                        ),
                    )
                await self._stop_current_process_locked()

            if self._process is None:
                try:
                    self._process = self._spawn_process(route, config)
                except OSError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail=f"Failed to start llama-server: {exc}",
                    ) from exc

                self._generation += 1
                self._current_route_name = route.name
                self._current_upstream_base_url = self._managed_upstream_base_url()
                self._current_idle_timeout_seconds = self._idle_timeout_for(config)

                try:
                    await self._wait_until_ready(
                        self._process,
                        timeout_seconds=self._startup_timeout_for(config),
                    )
                except Exception:
                    await self._stop_current_process_locked()
                    raise

            self._active_requests += 1
            self._last_used_monotonic = time.monotonic()
            return UpstreamLease(
                upstream_base_url=self._current_upstream_base_url or route.upstream_base_url,
                _release=self._release_request,
            )

    async def shutdown(self) -> None:
        lock = self._get_lock()
        async with lock:
            self._cancel_idle_shutdown_locked()
            await self._stop_current_process_locked()

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _managed_model_for_route(self, route: ModelRoute) -> ManagedLlamaModel:
        models = self._managed_models_map()
        config = models.get(route.name)
        if config is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"Managed llama-server route '{route.name}' is missing from "
                    "MODEL_ROUTER_MANAGED_LLAMA_MODELS_JSON"
                ),
            )
        return config

    def _managed_models_map(self) -> dict[str, ManagedLlamaModel]:
        if self._managed_models is not None:
            return self._managed_models

        raw = self._settings.managed_llama_models_json
        if not raw:
            self._managed_models = {}
            return self._managed_models

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MODEL_ROUTER_MANAGED_LLAMA_MODELS_JSON must be valid JSON",
            ) from exc

        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MODEL_ROUTER_MANAGED_LLAMA_MODELS_JSON must be a JSON object keyed by route name",
            )

        models: dict[str, ManagedLlamaModel] = {}
        for route_name, item in payload.items():
            if not isinstance(route_name, str) or not isinstance(item, dict):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Each managed llama-server entry must be an object keyed by model route name",
                )

            model_path = str(item.get("model_path") or "").strip()
            if not model_path:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Managed llama-server route '{route_name}' is missing model_path",
                )

            cli_args = item.get("cli_args") or []
            if not isinstance(cli_args, list):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=f"Managed llama-server route '{route_name}' cli_args must be a JSON array",
                )

            rendered_args = [str(arg) for arg in cli_args]
            if any(arg in _RESERVED_LLAMA_ARGS for arg in rendered_args):
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=(
                        f"Managed llama-server route '{route_name}' cli_args may not include "
                        "-m/--model, --host, --port, or --alias"
                    ),
                )

            models[route_name] = ManagedLlamaModel(
                model_path=model_path,
                cli_args=rendered_args,
                idle_timeout_seconds=self._int_or_none(item.get("idle_timeout_seconds")),
                startup_timeout_seconds=self._int_or_none(item.get("startup_timeout_seconds")),
            )

        self._managed_models = models
        return self._managed_models

    def _spawn_process(self, route: ModelRoute, config: ManagedLlamaModel) -> subprocess.Popen:
        command = [
            self._settings.managed_llama_command,
            "-m",
            config.model_path,
            "--host",
            self._settings.managed_llama_host,
            "--port",
            str(self._settings.managed_llama_port),
            "--alias",
            route.upstream_model_name,
            *config.cli_args,
        ]
        return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    async def _wait_until_ready(self, process: subprocess.Popen, *, timeout_seconds: int) -> None:
        deadline = time.monotonic() + timeout_seconds
        urls = [
            f"http://{self._settings.managed_llama_host}:{self._settings.managed_llama_port}{self._health_path()}",
            f"{self._managed_upstream_base_url()}/models",
        ]

        async with httpx.AsyncClient(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="llama-server exited before it became ready",
                    )

                for url in urls:
                    try:
                        response = await client.get(url)
                    except httpx.HTTPError:
                        continue
                    if response.status_code < 500:
                        return

                await asyncio.sleep(1)

        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Timed out waiting for llama-server to become ready",
        )

    async def _release_request(self) -> None:
        lock = self._get_lock()
        async with lock:
            if self._active_requests > 0:
                self._active_requests -= 1
            self._last_used_monotonic = time.monotonic()
            if self._active_requests == 0:
                self._schedule_idle_shutdown_locked()

    def _schedule_idle_shutdown_locked(self) -> None:
        idle_timeout = self._current_idle_timeout_seconds
        if idle_timeout is None or idle_timeout <= 0:
            return
        self._cancel_idle_shutdown_locked()
        self._idle_task = asyncio.create_task(
            self._idle_shutdown_after(idle_timeout=idle_timeout, generation=self._generation)
        )

    async def _idle_shutdown_after(self, *, idle_timeout: int, generation: int) -> None:
        try:
            await asyncio.sleep(idle_timeout)
            lock = self._get_lock()
            async with lock:
                if generation != self._generation:
                    return
                if self._active_requests > 0:
                    return
                if time.monotonic() - self._last_used_monotonic < idle_timeout:
                    return
                await self._stop_current_process_locked()
        except asyncio.CancelledError:
            return

    def _cancel_idle_shutdown_locked(self) -> None:
        if self._idle_task is None:
            return
        self._idle_task.cancel()
        self._idle_task = None

    async def _stop_current_process_locked(self) -> None:
        process = self._process
        self._process = None
        self._current_route_name = None
        self._current_upstream_base_url = None
        self._current_idle_timeout_seconds = None
        self._generation += 1

        if process is None:
            return

        await asyncio.to_thread(self._terminate_process, process)

    def _terminate_process(self, process: subprocess.Popen) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    def _managed_upstream_base_url(self) -> str:
        return f"http://{self._settings.managed_llama_host}:{self._settings.managed_llama_port}/v1"

    def _health_path(self) -> str:
        path = self._settings.managed_llama_health_path.strip() or "/health"
        return path if path.startswith("/") else f"/{path}"

    def _startup_timeout_for(self, config: ManagedLlamaModel) -> int:
        timeout = config.startup_timeout_seconds or self._settings.managed_llama_startup_timeout_seconds
        return max(timeout, 1)

    def _idle_timeout_for(self, config: ManagedLlamaModel) -> int | None:
        timeout = config.idle_timeout_seconds
        if timeout is None:
            timeout = self._settings.managed_llama_idle_timeout_seconds
        return timeout if timeout > 0 else None

    def _int_or_none(self, value: object) -> int | None:
        if value is None:
            return None
        try:
            rendered = int(value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Managed llama-server numeric fields must be integers",
            ) from exc
        if rendered < 0:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Managed llama-server numeric fields may not be negative",
            )
        return rendered


@lru_cache(maxsize=1)
def get_llama_server_manager() -> LlamaServerManager:
    return LlamaServerManager(get_settings())


async def shutdown_llama_server_manager() -> None:
    manager = get_llama_server_manager()
    try:
        await manager.shutdown()
    finally:
        get_llama_server_manager.cache_clear()
