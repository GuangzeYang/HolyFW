#!/usr/bin/env python3
"""DeepSeek-backed implementation of the abstract model request interface."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from common.agent_request_abc import AgentRequestABC, AgentRequestError, AgentResponse, AgentTimeoutError

DEEPSEEK_API_KEY_ENV = "DEEPSEEK_API_KEY"


@dataclass(slots=True)
class DeepSeekConfig:
    api_base_url: str
    api_key: str
    model: str
    request_timeout_seconds: int
    max_tokens: int


class DeepSeekAgentClient(AgentRequestABC):
    """DeepSeek implementation using the OpenAI-compatible chat completions API."""

    def __init__(self, config: DeepSeekConfig) -> None:
        self.config = config

    @property
    def provider_name(self) -> str:
        return "deepseek"

    @property
    def model(self) -> str:
        return self.config.model

    @property
    def request_timeout_seconds(self) -> int:
        return self.config.request_timeout_seconds

    def request_completion(
        self,
        prompt: str = "",
        *,
        messages: list[dict[str, str]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AgentResponse:
        endpoint = _normalize_endpoint(self.config.api_base_url)
        chat_messages = messages if messages else [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": chat_messages,
            "stream": False,
            "max_tokens": self.config.max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        start = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.config.request_timeout_seconds) as response:
                raw_text = response.read().decode("utf-8", errors="replace")
                status_code = int(getattr(response, "status", 200))
        except urllib.error.HTTPError as exc:
            elapsed = time.monotonic() - start
            raw_text = exc.read().decode("utf-8", errors="replace")
            raise AgentRequestError(
                f"DeepSeek API returned HTTP {exc.code}",
                status_code=exc.code,
                response_text=raw_text,
                elapsed_seconds=elapsed,
            ) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
            elapsed = time.monotonic() - start
            raise AgentTimeoutError(
                f"DeepSeek API request timed out or failed to connect: {exc}",
                response_text="",
                elapsed_seconds=elapsed,
            ) from None

        elapsed = time.monotonic() - start
        try:
            payload_json = json.loads(raw_text)
        except json.JSONDecodeError:
            raise AgentRequestError(
                "DeepSeek API response was not valid JSON",
                status_code=status_code,
                response_text=raw_text,
                elapsed_seconds=elapsed,
            ) from None

        response_text = _extract_message_content(payload_json)
        model = payload_json.get("model") if isinstance(payload_json.get("model"), str) else self.config.model
        return AgentResponse(
            model=model,
            response_text=response_text,
            status_code=status_code,
            elapsed_seconds=elapsed,
            raw_response_text=raw_text,
            finish_reason=_extract_finish_reason(payload_json),
        )


def build_deepseek_client(generator_config: dict[str, Any]) -> DeepSeekAgentClient:
    api_key = os.environ.get(DEEPSEEK_API_KEY_ENV, "").strip()
    if not api_key:
        raise ValueError(
            f"DeepSeek API key is missing; set the {DEEPSEEK_API_KEY_ENV} environment variable"
        )
    config = DeepSeekConfig(
        api_base_url=str(generator_config["api_base_url"]),
        api_key=api_key,
        model=str(generator_config["model"]),
        request_timeout_seconds=int(generator_config["request_timeout_seconds"]),
        max_tokens=int(generator_config["max_tokens"]),
    )
    return DeepSeekAgentClient(config)


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


def _extract_finish_reason(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    first = choices[0]
    if not isinstance(first, dict):
        return None
    finish_reason = first.get("finish_reason")
    return finish_reason if isinstance(finish_reason, str) and finish_reason.strip() else None
