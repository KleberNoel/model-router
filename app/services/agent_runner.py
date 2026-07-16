from typing import Any


def completion_payload(run: Any) -> dict:
    return {
        "model": run.model_route,
        "messages": [{"role": "user", "content": run.input_text}],
        "stream": False,
    }


def completion_text(response: dict) -> str:
    choices = response.get("choices") or []
    if not choices:
        raise ValueError("Model response did not contain choices")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("Model response did not contain text content")
    return content
