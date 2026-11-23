"""OpenAI-compatible chat client with tool calling, aimed at a local server (Ollama, mlx-lm, llama.cpp).

Defaults point at Ollama on localhost running Inkling 2 Mini. Override with:
  DOT_MODEL_URL  base URL ending in /v1     (default http://127.0.0.1:11434/v1)
  DOT_MODEL      model name                  (default hf.co/ggml-org/Inkling-2-Mini-GGUF:IQ4_NL: the 4-bit build that fits a 24 GB Mac)
  DOT_API_KEY    bearer token, if the server wants one (hosted fallback)
  DOT_REASONING  reasoning_effort to send (default "low": short thinking = fast steps; "" omits it)
  DOT_MAX_TOKENS max output tokens per step (default 2048; caps runaway reasoning)
"""
from __future__ import annotations

import contextlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol


# Inkling 2 Mini, 4-bit GGUF (~15 GB) pulled straight from Hugging Face by Ollama. Dot never switches to
# another model on its own: if this one isn't on the server, it says so and stops.
DEFAULT_MODEL = "hf.co/ggml-org/Inkling-2-Mini-GGUF:IQ4_NL"


class ModelError(RuntimeError):
    """A model call failed; the message says what to do about it."""


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
    served_model: str = ""  # the model name the server says answered


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
        raw = fn.get("arguments")
        if raw is None:  # missing or null = no arguments
            raw = ""
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
        self.model = model or os.environ.get("DOT_MODEL") or DEFAULT_MODEL
        self.api_key = api_key if api_key is not None else os.environ.get("DOT_API_KEY", "")
        self.reasoning = os.environ.get("DOT_REASONING", "low")
        self.max_tokens = max_tokens or int(os.environ.get("DOT_MAX_TOKENS", "2048"))
        self.timeout = timeout

    def _request(self, path: str, body: dict[str, Any] | None = None, timeout: float | None = None) -> Any:
        headers = {"Content-Type": "application/json", "User-Agent": "dot-agent/0"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}{path}", headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read())

    def explain(self, e: Exception, detail: str = "") -> ModelError:
        """Turn a raw HTTP/network failure into a message that says what to do."""
        url = self.base_url
        if isinstance(e, urllib.error.HTTPError):
            detail = (detail or _body(e))[:300]
            if e.code == 404 or "not found" in detail.lower():
                names = self.available()
                hint = f" Models on that server: {', '.join(names)}." if names else ""
                return ModelError(f"Model '{self.model}' not found at {url}.{hint} Pull it (`ollama pull "
                                  f"{self.model}`) or set DOT_MODEL to one that exists. [HTTP {e.code}: {detail}]")
            if e.code in (401, 403):
                return ModelError(f"The model server at {url} refused the key (HTTP {e.code}). Check DOT_API_KEY.")
            return ModelError(f"The model server at {url} returned HTTP {e.code}: {detail or e.reason}")
        if isinstance(e, urllib.error.URLError | ConnectionError | TimeoutError):
            reason = getattr(e, "reason", e)
            return ModelError(f"No model server answering at {url} ({reason}). Start it (`ollama serve`, then "
                              f"`ollama pull {self.model}`) or set DOT_MODEL_URL.")
        return ModelError(f"Model call to {url} failed: {type(e).__name__}: {e}")

    def available(self) -> list[str]:
        """Model names the server lists (OpenAI-compatible /models); [] if it can't say."""
        try:
            data = self._request("/models", timeout=5)
            items = data.get("data") if isinstance(data, dict) else None
            return [str(m.get("id")) for m in items if isinstance(m, dict)] if isinstance(items, list) else []
        except Exception:  # noqa: BLE001 - best effort, used for hints
            return []

    def check(self) -> str | None:
        """Startup check: None if the server is up and has the model, else what to fix."""
        try:
            data = self._request("/models", timeout=5)
        except urllib.error.HTTPError as e:
            if e.code == 404:  # server has no /models route; chat may still work, so don't alarm
                return None
            return str(self.explain(e))
        except Exception as e:  # noqa: BLE001
            return str(self.explain(e))
        items = data.get("data") if isinstance(data, dict) else None
        names = [str(m.get("id")) for m in items if isinstance(m, dict)] if isinstance(items, list) else []
        if names and not any(n == self.model or n.split(":")[0] == self.model for n in names):
            return (f"Model '{self.model}' isn't on the server at {self.base_url} (it has: {', '.join(names)}). "
                    f"Run `ollama pull {self.model}` or set DOT_MODEL.")
        return None

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Reply:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = tools
        if self.reasoning:
            body["reasoning_effort"] = self.reasoning
        try:
            try:
                data = self._request("/chat/completions", body)
            except urllib.error.HTTPError as e:
                detail = _body(e)
                # Some servers reject reasoning_effort for this model: drop it for good and retry once.
                if not (e.code == 400 and self.reasoning and any(w in detail.lower() for w in ("reason", "think"))):
                    raise self.explain(e, detail) from e
                self.reasoning = ""
                body.pop("reasoning_effort", None)
                data = self._request("/chat/completions", body)
        except ModelError:
            raise
        except Exception as e:  # noqa: BLE001
            raise self.explain(e) from e
        reply = parse_message(data["choices"][0]["message"])
        reply.usage = data.get("usage") or {}
        reply.served_model = str(data.get("model") or "")
        return reply


def _body(e: urllib.error.HTTPError) -> str:
    with contextlib.suppress(Exception):
        return e.read().decode("utf-8", errors="replace")
    return ""
