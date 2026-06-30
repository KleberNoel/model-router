import unittest
from unittest.mock import AsyncMock, Mock

from fastapi import HTTPException

from app.config import Settings
from app.models import ModelRoute
from app.services.model_manager import LlamaServerManager, MANAGED_LLAMA_UPSTREAM


class _FakeProcess:
    def __init__(self, *, exit_code=None):
        self.exit_code = exit_code
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = 0

    def kill(self):
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout=None):
        return self.exit_code


def _route(name: str, upstream_model_name: str | None = None) -> ModelRoute:
    return ModelRoute(
        name=name,
        upstream_base_url=MANAGED_LLAMA_UPSTREAM,
        upstream_model_name=upstream_model_name or name,
    )


class LlamaServerManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_reuses_loaded_model(self):
        settings = Settings(
            managed_llama_models_json='{"alpha":{"model_path":"/models/alpha.gguf"}}'
        )
        manager = LlamaServerManager(settings)
        process = _FakeProcess()
        manager._spawn_process = Mock(return_value=process)
        manager._wait_until_ready = AsyncMock()

        first = await manager.acquire(_route("alpha"))
        second = await manager.acquire(_route("alpha"))

        self.assertEqual(first.upstream_base_url, "http://127.0.0.1:8090/v1")
        self.assertEqual(second.upstream_base_url, "http://127.0.0.1:8090/v1")
        self.assertEqual(manager._spawn_process.call_count, 1)

        await second.release()
        await first.release()
        await manager.shutdown()

    async def test_switches_models_when_idle(self):
        settings = Settings(
            managed_llama_models_json=(
                '{"alpha":{"model_path":"/models/alpha.gguf"},'
                '"beta":{"model_path":"/models/beta.gguf"}}'
            )
        )
        manager = LlamaServerManager(settings)
        alpha_process = _FakeProcess()
        beta_process = _FakeProcess()
        manager._spawn_process = Mock(side_effect=[alpha_process, beta_process])
        manager._wait_until_ready = AsyncMock()

        alpha = await manager.acquire(_route("alpha"))
        await alpha.release()
        beta = await manager.acquire(_route("beta"))

        self.assertTrue(alpha_process.terminated)
        self.assertEqual(manager._spawn_process.call_count, 2)

        await beta.release()
        await manager.shutdown()

    async def test_rejects_switch_while_request_is_active(self):
        settings = Settings(
            managed_llama_models_json=(
                '{"alpha":{"model_path":"/models/alpha.gguf"},'
                '"beta":{"model_path":"/models/beta.gguf"}}'
            )
        )
        manager = LlamaServerManager(settings)
        manager._spawn_process = Mock(return_value=_FakeProcess())
        manager._wait_until_ready = AsyncMock()

        alpha = await manager.acquire(_route("alpha"))

        with self.assertRaises(HTTPException) as exc_info:
            await manager.acquire(_route("beta"))

        self.assertEqual(exc_info.exception.status_code, 503)

        await alpha.release()
        await manager.shutdown()
