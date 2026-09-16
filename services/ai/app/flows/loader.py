"""Loading and validating flow definitions from YAML."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from app.flows.schema import Flow

# Flows live beside the service rather than inside the package: they are
# configuration, meant to be edited without touching Python.
FLOWS_DIR = Path(__file__).resolve().parents[2] / "flows"

DEFAULT_FLOW_ID = "servicing"


@lru_cache(maxsize=1)
def load_flows() -> dict[str, Flow]:
    """
    Read every flow definition, validating as we go.

    Validation failures raise at startup rather than mid-conversation. A flow
    with a typo in a state name would otherwise present as a call that silently
    dead-ends, which is near-impossible to diagnose from a transcript.
    """
    flows: dict[str, Flow] = {}

    for path in sorted(FLOWS_DIR.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text())
        if not raw:
            continue
        try:
            flow = Flow.model_validate(raw)
        except Exception as exc:
            raise ValueError(f"{path.name} is not a valid flow: {exc}") from exc
        if flow.id in flows:
            raise ValueError(f"duplicate flow id '{flow.id}' in {path.name}")
        flows[flow.id] = flow

    if not flows:
        raise ValueError(f"No flow definitions found in {FLOWS_DIR}")
    return flows


def get_flow(flow_id: str | None = None) -> Flow:
    flows = load_flows()
    return flows.get(flow_id or DEFAULT_FLOW_ID) or flows[DEFAULT_FLOW_ID]
