"""
Flows as a soft state machine.

A flow narrows the tool set per conversational state. That narrowing is the
whole mechanism — measured at 73% → 81% tool-selection accuracy — so the tests
here assert on what a state *offers*, not on what a model then chose to do.
"""

from __future__ import annotations

from app.flows.loader import get_flow, load_flows
from app.orchestrator.turn import run_turn
from app.tools.base import registry


def test_every_tool_a_flow_names_is_registered() -> None:
    """
    The same check the service runs at startup, as a test.

    A typo here presents as a state whose tools silently do not appear: the
    agent refuses to do something the flow says it can, with nothing in the logs
    to explain why.
    """
    known = set(registry.tools)

    for flow in load_flows().values():
        for name, state in flow.states.items():
            unknown = set(state.tools) - known
            assert not unknown, f"flow '{flow.id}' state '{name}' names {unknown}"


def test_the_initial_state_exists() -> None:
    for flow in load_flows().values():
        assert flow.initial in flow.states, f"flow '{flow.id}' starts nowhere"


def test_states_narrow_rather_than_offer_everything() -> None:
    """
    The point of scoping. If a state offered every tool, the flow would be
    documentation rather than a mechanism.
    """
    flow = get_flow("servicing")
    everything = len(registry.tools)

    assert len(flow.state(flow.initial).tools) < everything


async def test_an_unknown_state_falls_back_to_the_initial_one(scripted, call) -> None:
    """
    Soft, not strict. A state that no longer exists — an old call resumed after
    a flow was edited — must not strand the conversation; it starts over rather
    than failing.
    """
    result = await run_turn(
        "is AI858 on time?",
        llm=scripted([call("check_flight_status", flight_number="AI858")], "On time."),
        flow_id="servicing",
        flow_state="a-state-that-was-deleted",
    )

    assert result.flow_state in get_flow("servicing").states


async def test_the_model_is_only_offered_the_states_tools(scripted, call) -> None:
    """Asserts on what was offered, rather than inferring it from the outcome."""
    llm = scripted([call("check_flight_status", flight_number="AI858")], "On time.")
    flow = get_flow("servicing")

    await run_turn("is AI858 on time?", llm=llm, flow_id="servicing", flow_state=flow.initial)

    assert llm.tools_offered[0] == list(flow.state(flow.initial).tools)


async def test_the_final_iteration_withholds_tools_entirely(scripted, call) -> None:
    """
    Asking a model to "stop calling tools" is unreliable; removing them is not.
    Without this, a model that keeps requesting tools loops until something else
    times out.
    """
    from app.config import settings

    # More tool calls than the loop will allow.
    turns = [[call("check_flight_status", flight_number="AI858")]] * (settings.max_tool_iterations + 2)
    llm = scripted(*turns)

    result = await run_turn("is AI858 on time?", llm=llm)

    assert result.iterations <= settings.max_tool_iterations
    assert llm.tools_offered[-1] == [], "the last pass still offered tools"
