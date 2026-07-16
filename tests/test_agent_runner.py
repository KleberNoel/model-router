from types import SimpleNamespace

from app.services.agent_runner import completion_text, completion_payload


def test_completion_payload_uses_profile_route_and_run_input():
    run = SimpleNamespace(model_route="agent/hermes", input_text="List my tasks")

    assert completion_payload(run) == {
        "model": "agent/hermes",
        "messages": [{"role": "user", "content": "List my tasks"}],
        "stream": False,
    }


def test_completion_text_extracts_openai_message():
    response = {"choices": [{"message": {"content": "Here are your tasks."}}]}

    assert completion_text(response) == "Here are your tasks."
