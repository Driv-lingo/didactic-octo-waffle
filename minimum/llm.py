"""Model client abstraction.

Every agent talks to a ``ModelClient`` that returns a validated pydantic
object. The production client is the Anthropic SDK with structured outputs.
``FakeClient`` is used by the tests and by ``--offline`` runs.
"""

from __future__ import annotations

import os
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

DEFAULT_MODEL = os.environ.get("MINIMUM_MODEL", "claude-opus-5-5")


class ModelClient(Protocol):
    def complete(
        self,
        *,
        role: str,
        system: str,
        messages: list[dict],
        schema: type[T],
        effort: str = "high",
    ) -> T: ...


class AnthropicClient:
    """Structured-output calls through the official SDK.

    The system prompt is cached (it is the stable prefix for every call an
    agent makes), and the examiner/verifier run at higher effort than the tutor.
    """

    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 16000):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()
        self.model = model
        self.max_tokens = max_tokens

    def complete(self, *, role: str, system: str, messages: list[dict], schema: type[T], effort: str = "high") -> T:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_format=schema,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
        )
        if response.stop_reason == "refusal":
            detail = ""
            if response.stop_details is not None:
                detail = f" ({response.stop_details.category}: {response.stop_details.explanation})"
            raise RuntimeError(f"{role}: model declined the request{detail}")
        if response.parsed_output is None:
            raise RuntimeError(f"{role}: model returned no structured output (stop_reason={response.stop_reason})")
        return response.parsed_output


class FakeClient:
    """Scripted client for tests and offline use.

    ``handler(role, system, messages, schema)`` returns an instance of
    ``schema`` (or a dict to validate). Every call is recorded so tests can
    assert what each agent was shown.
    """

    def __init__(self, handler: Callable[[str, str, list[dict], type[BaseModel]], BaseModel | dict]):
        self.handler = handler
        self.calls: list[dict] = []

    def complete(self, *, role: str, system: str, messages: list[dict], schema: type[T], effort: str = "high") -> T:
        self.calls.append({"role": role, "system": system, "messages": messages, "schema": schema, "effort": effort})
        out = self.handler(role, system, messages, schema)
        if isinstance(out, dict):
            out = schema.model_validate(out)
        return out  # type: ignore[return-value]


def default_client(offline: bool = False) -> ModelClient:
    """The production client, or the scripted offline client on request.

    Credentials are resolved by the SDK (API key, auth token, or an
    ``ant auth login`` profile), so an unset API key is not treated as
    "no credentials" here; a missing credential surfaces on the first call.
    """
    if offline:
        from .agents.offline import offline_handler

        return FakeClient(offline_handler)
    return AnthropicClient()
