"""
Tool abstraction and registry.

A tool is a backend operation the model may choose to run. Each declares a JSON
Schema the model sees, and returns a uniform result the orchestrator can turn
into a tool message without knowing what the tool did.

Failures are values, not exceptions. A tool that raises would abort the turn; a
tool that *returns* a failure lets the model explain the problem to the customer
in their own words, which is the whole point of putting an LLM in front of an API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.providers.base import ToolSpec


@dataclass(slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    # Carried through so the Phase 6 retry engine can decide whether a failed
    # turn is worth re-running, without re-deriving it from an HTTP status.
    retryable: bool = False
    duration_ms: int = 0

    def to_model_content(self) -> str:
        """What the model sees as the result of its call."""
        if self.ok:
            return json.dumps(self.data or {}, default=str)
        return json.dumps(
            {
                "error": self.error_code,
                "message": self.error_message,
                "retryable": self.retryable,
            }
        )

    @classmethod
    def success(cls, data: dict[str, Any], duration_ms: int = 0) -> "ToolResult":
        return cls(ok=True, data=data, duration_ms=duration_ms)

    @classmethod
    def failure(
        cls, code: str, message: str, *, retryable: bool = False, duration_ms: int = 0
    ) -> "ToolResult":
        return cls(
            ok=False, error_code=code, error_message=message,
            retryable=retryable, duration_ms=duration_ms,
        )


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Awaitable[ToolResult]]
    # Tools that change state. The Phase 5 flow engine requires explicit customer
    # confirmation before running any of these, which is why it is declared here
    # rather than inferred from the name.
    mutating: bool = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


@dataclass
class ToolRegistry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> Tool:
        if tool.name in self.tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self.tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def specs(self, names: list[str] | None = None) -> list[ToolSpec]:
        """
        Tool descriptions for the model, optionally narrowed to a named subset.

        The flow engine uses the subset form: a small model offered two tools
        chooses far better than the same model offered seven.
        """
        if names is None:
            return [tool.spec for tool in self.tools.values()]
        return [self.tools[name].spec for name in names if name in self.tools]

    async def run(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            # A hallucinated tool name. Told plainly to the model, which then
            # picks a real one instead of the turn failing outright.
            return ToolResult.failure(
                "UNKNOWN_TOOL",
                f"No tool named '{name}'. Available: {', '.join(sorted(self.tools))}",
            )

        # Reject unexpected arguments rather than passing them to the handler,
        # where they would surface as an unhelpful TypeError.
        allowed = set(tool.parameters.get("properties", {}))
        unexpected = set(arguments) - allowed
        if unexpected:
            return ToolResult.failure(
                "INVALID_ARGUMENTS",
                f"{tool.name} does not accept: {', '.join(sorted(unexpected))}. "
                f"Accepted: {', '.join(sorted(allowed))}",
            )

        missing = set(tool.parameters.get("required", [])) - set(arguments)
        if missing:
            return ToolResult.failure(
                "MISSING_ARGUMENTS",
                f"{tool.name} requires: {', '.join(sorted(missing))}",
            )

        return await tool.handler(**arguments)


registry = ToolRegistry()
