"""
Flow definitions.

A flow is a soft state machine over a conversation. Each state declares which
tools are reachable from it; the model still drives the dialogue in its own
words, but it can only act within the current state.

The point is not to script the customer. It is that a 7B model offered seven
tools picks worse than the same model offered two — measured in Phase 3, where
prompt length and tool count traded directly against selection accuracy. Narrowing
the choice is the structural fix that no amount of prompt wording buys.

A second benefit falls out of it: which operations are reachable, and from where,
becomes a data file a non-programmer can read, rather than control flow buried in
an orchestrator.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class Transition(BaseModel):
    """Move to another state when a tool resolves."""

    tool: str
    to: str
    # Whether this fires on success or on failure. Failure transitions matter:
    # a booking that could not be found should not advance to a state that
    # assumes one is loaded.
    on: str = Field(default="success", pattern="^(success|failure)$")


class State(BaseModel):
    # Documentation for whoever reads this file. Deliberately not sent to the
    # model: injecting it cost 12-28% of tool-selection accuracy (see
    # app/orchestrator/turn.py). The tool list is the instruction.
    purpose: str = ""
    # Tools reachable here. An empty list means the agent can only talk.
    tools: list[str] = Field(default_factory=list)
    transitions: list[Transition] = Field(default_factory=list)
    # A terminal state ends the useful part of the call; the agent can still
    # speak, but the work is done.
    terminal: bool = False


class Flow(BaseModel):
    id: str
    description: str = ""
    initial: str
    states: dict[str, State]

    @model_validator(mode="after")
    def _check_references(self) -> "Flow":
        """
        Fail loudly at load time rather than mid-call.

        A typo in a state name would otherwise surface as a conversation that
        silently dead-ends, which is close to impossible to debug from a
        transcript.
        """
        if self.initial not in self.states:
            raise ValueError(f"flow '{self.id}': initial state '{self.initial}' is not defined")

        for name, state in self.states.items():
            for transition in state.transitions:
                if transition.to not in self.states:
                    raise ValueError(
                        f"flow '{self.id}': state '{name}' transitions to "
                        f"'{transition.to}', which is not defined"
                    )
                if transition.tool not in state.tools:
                    raise ValueError(
                        f"flow '{self.id}': state '{name}' transitions on tool "
                        f"'{transition.tool}', which it does not offer"
                    )
        return self

    def state(self, name: str | None) -> State:
        """The named state, falling back to the initial one."""
        return self.states.get(name or self.initial) or self.states[self.initial]

    def next_state(self, current: str | None, tool: str, succeeded: bool) -> str | None:
        """Where this tool result leads, or None to stay put."""
        wanted = "success" if succeeded else "failure"
        for transition in self.state(current).transitions:
            if transition.tool == tool and transition.on == wanted:
                return transition.to
        return None
