"""A minimal Ollama backend (stdlib only) with the same call shape as the Anthropic client.

`OllamaClient().messages.create(model=, system=, messages=, ...)` posts to /api/chat with
thinking off, no streaming, temperature 1 and a 3-token reply budget, and returns an object
with `.content`, `.stop_reason` and `.usage`, so `readout.episode.run_episode` runs unchanged.
The system message goes first in the message list; the multi-turn history follows as is.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

OLLAMA_URL = "http://localhost:11434/api/chat"
OPTIONS = {"temperature": 1, "num_predict": 3}


class OllamaError(Exception):
    pass


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class OllamaUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class OllamaResponse:
    content: list[TextBlock]
    stop_reason: str
    usage: OllamaUsage


class OllamaClient:
    def __init__(self, url: str = OLLAMA_URL, timeout: float = 120.0) -> None:
        self.url = url
        self.timeout = timeout
        self.messages = self  # so client.messages.create(...) works

    def create(self, *, model: str, system: str, messages: list[dict[str, Any]], **_: Any) -> OllamaResponse:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "think": False,
            "stream": False,
            "options": OPTIONS,
        }
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            raise OllamaError(f"{type(e).__name__}: {e}") from e
        if "error" in data:
            raise OllamaError(data["error"])
        return OllamaResponse(
            content=[TextBlock(data["message"]["content"])],
            stop_reason="max_tokens" if data.get("done_reason") == "length" else "end_turn",
            usage=OllamaUsage(data.get("prompt_eval_count", 0), data.get("eval_count", 0)),
        )
