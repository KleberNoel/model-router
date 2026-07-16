import importlib.util
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "kokoro_tts.py"
    spec = importlib.util.spec_from_file_location("kokoro_tts_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_openai_voice_aliases_resolve_to_kokoro_voices():
    module = _module()
    module.KOKORO_VOICES[:] = ["af_heart", "af_bella"]

    assert module.resolve_voice("af_heart") == "af_heart"
    assert module.resolve_voice("alloy") == "af_heart"


def test_openai_voice_payload_contains_discovered_voice_objects():
    module = _module()
    module.KOKORO_VOICES[:] = ["af_heart", "bf_emma"]

    payload = module.openai_voice_payload()

    assert payload == {
        "voices": [
            {"id": "af_heart", "name": "af_heart"},
            {"id": "bf_emma", "name": "bf_emma"},
        ]
    }
