#!/usr/bin/env python3
"""Abstract request interfaces for model-backed task generation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AgentResponse:
    model: str
    response_text: str
    status_code: int
    elapsed_seconds: float
    raw_response_text: str
    finish_reason: str | None = None


class AgentRequestError(Exception):
    """Raised when a model provider returns an error or invalid payload."""

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


class AgentTimeoutError(AgentRequestError):
    """Raised when a model provider request times out."""


class AgentRequestABC(ABC):
    """Abstract interface for requesting a completion from a model provider."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def model(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def request_timeout_seconds(self) -> int:
        raise NotImplementedError

    @abstractmethod
    def request_completion(
        self,
        prompt: str = "",
        *,
        messages: list[dict[str, str]] | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> AgentResponse:
        raise NotImplementedError
