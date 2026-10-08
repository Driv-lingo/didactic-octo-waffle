from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from ..llm import ModelClient

T = TypeVar("T", bound=BaseModel)


class Agent:
    role: str = "agent"
    system: str = ""
    effort: str = "high"

    def __init__(self, client: ModelClient):
        self.client = client

    def ask(self, messages: list[dict], schema: type[T]) -> T:
        return self.client.complete(
            role=self.role, system=self.system, messages=messages, schema=schema, effort=self.effort
        )

    @staticmethod
    def user(text: str) -> dict:
        return {"role": "user", "content": text}

    @staticmethod
    def assistant(text: str) -> dict:
        return {"role": "assistant", "content": text}
