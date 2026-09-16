"""
Runtime control for the chaos engine.

Deliberately exempt from chaos injection — if `hard_down` could break this
router too, there would be no way to turn it back off.
"""

from __future__ import annotations

from fastapi import APIRouter

from app import chaos
from app.chaos import SCENARIOS, ChaosConfig, state

router = APIRouter(prefix="/admin", tags=["admin"])


def _snapshot() -> dict:
    total = state.injected + state.passed
    return {
        "config": state.config.model_dump(),
        "stats": {
            "requests_seen": total,
            "failures_injected": state.injected,
            "passed_through": state.passed,
            "observed_error_rate": round(state.injected / total, 3) if total else 0.0,
        },
    }


@router.get("/chaos")
async def get_chaos() -> dict:
    return _snapshot()


@router.put("/chaos")
async def set_chaos(config: ChaosConfig) -> dict:
    """Replace the entire chaos configuration."""
    state.config = config
    return _snapshot()


@router.patch("/chaos")
async def patch_chaos(patch: dict) -> dict:
    """Change individual knobs without restating the whole config."""
    merged = state.config.model_dump() | patch
    state.config = ChaosConfig.model_validate(merged)
    return _snapshot()


@router.get("/chaos/scenarios")
async def list_scenarios() -> dict:
    return {name: cfg.model_dump() for name, cfg in SCENARIOS.items()}


@router.post("/chaos/scenario/{name}")
async def apply_scenario(name: str) -> dict:
    if name not in SCENARIOS:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail={
                "code": "UNKNOWN_SCENARIO",
                "message": f"No scenario named {name}. Known: {', '.join(SCENARIOS)}",
                "retryable": False,
            },
        )
    state.config = SCENARIOS[name].model_copy(deep=True)
    state.reset_counters()
    return {"applied": name} | _snapshot()


@router.post("/chaos/reset")
async def reset_chaos() -> dict:
    state.config = chaos.ChaosConfig()
    state.reset_counters()
    return _snapshot()
