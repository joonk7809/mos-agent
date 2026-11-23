"""OpenAI-compatible chat client with tool calling, aimed at a local server (Ollama, mlx-lm, llama.cpp).

Defaults point at Ollama on localhost running Inkling 2 Mini. Override with:
  DOT_MODEL_URL  base URL ending in /v1     (default http://127.0.0.1:11434/v1)
  DOT_MODEL      model name                  (default inkling-2-mini)
  DOT_API_KEY    bearer token, if the server wants one (hosted fallback)
  DOT_REASONING  reasoning_effort to send (default "low": short thinking = fast steps; "" omits it)
  DOT_MAX_TOKENS max output tokens per step (default 2048; caps runaway reasoning)
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    parse_error: str | None = None


@dataclass
class Reply:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The assistant message exactly as it goes back into the history.
    message: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


class Model(Protocol):
    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply: ...


def parse_message(msg: dict[str, Any]) -> Reply:
    """Turn an OpenAI-style assistant message into a Reply. Bad tool-call JSON is kept, not raised."""
    content = msg.get("content") or ""
    if not isinstance(content, str):  # some servers return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    calls: list[ToolCall] = []
    for i, tc in enumerate(msg.get("tool_calls") or []):
        fn = tc.get("function") or {}
        raw = fn.get("arguments", "")
        args: dict[str, Any] = {}
        err = None
        if isinstance(raw, dict):  # Ollama sometimes sends a dict already
            args, raw = raw, json.dumps(raw)
        elif not isinstance(raw, str):
            err, raw = "arguments must be a JSON object", json.dumps(raw, default=str)
        if err is None and not args:
            try:
                parsed = json.loads(raw) if raw else {}
                if isinstance(parsed, dict):
                    args = parsed
                else:
                    err = "arguments must be a JSON object"
            except (json.JSONDecodeError, TypeError) as e:
                err = f"arguments are not valid JSON: {e}"
        calls.append(ToolCall(id=tc.get("id") or f"call_{i}", name=str(fn.get("name", "")),
                              arguments=args, raw_arguments=raw, parse_error=err))
    out_msg: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        out_msg["tool_calls"] = [{"id": c.id, "type": "function",
                                  # Bad arguments go back as "{}": servers re-parse history and would reject them.
                                  "function": {"name": c.name,
                                               "arguments": "{}" if c.parse_error else (c.raw_arguments or "{}")}}
                                 for c in calls]
    return Reply(content=content, tool_calls=calls, message=out_msg)


class OpenAICompatModel:
    def __init__(self, base_url: str | None = None, model: str | None = None, api_key: str | None = None,
                 max_tokens: int | None = None, timeout: float = 300.0):
        self.base_url = (base_url or os.environ.get("DOT_MODEL_URL") or "http://127.0.0.1:11434/v1").rstrip("/")
        self.model = model or os.environ.get("DOT_MODEL") or "inkling-2-mini"
        self.api_key = api_key if api_key is not None else os.environ.get("DOT_API_KEY", "")
        self.reasoning = os.environ.get("DOT_REASONING", "low")
        self.max_tokens = max_tokens or int(os.environ.get("DOT_MAX_TOKENS", "2048"))
        self.timeout = timeout

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = tools
        if self.reasoning:
            body["reasoning_effort"] = self.reasoning
        headers = {"Content-Type": "application/json", "User-Agent": "dot-agent/0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        reply = parse_message(data["choices"][0]["message"])
        reply.usage = data.get("usage") or {}
        return reply
