"""
Fixtures for the AI service tests.

The LLM is scripted, never real. The properties under test — that the gate
holds, that a flow narrows the tool set — must hold for *any* model output,
including a model that has been talked into ignoring its instructions. Testing
them against a cooperative model would prove nothing, and would make the suite
depend on a 5GB download and a GPU.
"""

from __future__ import annotations

# Importing the tools module is what registers them; without it every tool
# lookup returns None and the gate has nothing to gate.
import app.tools.flight  # noqa: F401
import pytest
from app.providers.base import LLMResponse, Message, ToolCall, ToolSpec


class ScriptedLLM:
    """An LLM that returns exactly what the test tells it to, in order."""

    name, model = "scripted", "scripted"

    def __init__(self, *turns: list[ToolCall] | str) -> None:
        self.turns = list(turns)
        self.calls_seen = 0
        self.tools_offered: list[list[str]] = []

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        self.calls_seen += 1
        # Recorded so a test can assert on what the flow actually offered,
        # rather than inferring it from what the model then did.
        self.tools_offered.append([t.name for t in tools] if tools else [])

        step = self.turns.pop(0) if self.turns else "Done."
        if isinstance(step, str):
            return LLMResponse(content=step, tool_calls=[], provider=self.name,
                               model=self.model, duration_ms=0)
        return LLMResponse(content=None, tool_calls=step, provider=self.name,
                           model=self.model, duration_ms=0)


@pytest.fixture
def scripted():
    return ScriptedLLM


@pytest.fixture
def call():
    def make(name: str, **arguments) -> ToolCall:
        return ToolCall(id=f"c{abs(hash(name)) % 9999}", name=name, arguments=arguments)
    return make


@pytest.fixture
def blocked():
    """Tool calls the confirmation gate stopped."""
    def pick(result) -> list[str]:
        return [i.name for i in result.tool_invocations
                if i.error_code == "CONFIRMATION_REQUIRED"]
    return pick


@pytest.fixture
def executed():
    """Tool calls that actually ran, whether or not they succeeded."""
    def pick(result) -> list[str]:
        return [i.name for i in result.tool_invocations
                if i.error_code != "CONFIRMATION_REQUIRED"]
    return pick
