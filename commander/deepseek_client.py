#!/usr/bin/env python3
"""Minimal DeepSeek API client for task generation."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class DeepSeekConfig:
    api_base_url: str
    api_key: str
    model: str
    request_timeout_seconds: int


@dataclass(slots=True)
class DeepSeekResponse:
    model: str
    response_text: str
    status_code: int
    elapsed_seconds: float
    raw_response_text: str


class DeepSeekAPIError(Exception):
    """Raised when the DeepSeek API returns an error or invalid payload."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str = "",
        elapsed_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text
        self.elapsed_seconds = elapsed_seconds


class DeepSeekTimeoutError(DeepSeekAPIError):
    """Raised when the DeepSeek API request times out."""


def build_deepseek_config(generator_config: dict[str, Any]) -> DeepSeekConfig:
    return DeepSeekConfig(
        api_base_url=str(generator_config["api_base_url"]),
        api_key=str(generator_config["api_key"]),
        model=str(generator_config["model"]),
        request_timeout_seconds=int(generator_config["request_timeout_seconds"]),
    )


def _normalize_endpoint(api_base_url: str) -> str:
    base = api_base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def request_deepseek_completion(prompt: str, config: DeepSeekConfig) -> DeepSeekResponse:
    endpoint = _normalize_endpoint(config.api_base_url)
    payload = {
        "model": config.model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key}",
        },
        method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=config.request_timeout_seconds) as response:
            raw_text = response.read().decode("utf-8", errors="replace")
            status_code = int(getattr(response, "status", 200))
    except urllib.error.HTTPError as exc:
        elapsed = time.monotonic() - start
        raw_text = exc.read().decode("utf-8", errors="replace")
        raise DeepSeekAPIError(
            f"DeepSeek API returned HTTP {exc.code}",
            status_code=exc.code,
            response_text=raw_text,
            elapsed_seconds=elapsed,
        ) from None
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        elapsed = time.monotonic() - start
        raise DeepSeekTimeoutError(
            f"DeepSeek API request timed out or failed to connect: {exc}",
            response_text="",
            elapsed_seconds=elapsed,
        ) from None

    elapsed = time.monotonic() - start
    try:
        payload_json = json.loads(raw_text)
    except json.JSONDecodeError:
        raise DeepSeekAPIError(
            "DeepSeek API response was not valid JSON",
            status_code=status_code,
            response_text=raw_text,
            elapsed_seconds=elapsed,
        ) from None

    response_text = _extract_message_content(payload_json)
    model = payload_json.get("model") if isinstance(payload_json.get("model"), str) else config.model
    return DeepSeekResponse(
        model=model,
        response_text=response_text,
        status_code=status_code,
        elapsed_seconds=elapsed,
        raw_response_text=raw_text,
    )
