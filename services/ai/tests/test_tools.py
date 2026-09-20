"""
Tool-layer behaviour that does not need a network.

Identifier normalisation and response-shape validation are the two places where
this service defends itself against the outside world: noisy speech on the way
in, and a sick upstream on the way out.
"""

from __future__ import annotations

import pytest
from app.tools.base import registry
from app.tools.flight import _EXPECTED_KEYS, _normalise_identifier


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        ("AI858", "AI858"),
        # Whisper reliably splits alphanumerics. Measured on both model sizes,
        # so it is a property of the task rather than of one model.
        ("AI 858", "AI858"),
        ("ai 858", "AI858"),
        ("AI-858", "AI858"),
        ("AI.858", "AI858"),
        ("A I 8 5 8", "AI858"),
        ("  6e 597  ", "6E597"),
        ("UYO-57P", "UYO57P"),
        ("uyo 57 p", "UYO57P"),
    ],
)
def test_transcription_noise_is_stripped(heard: str, expected: str) -> None:
    assert _normalise_identifier(heard) == expected


def test_normalisation_does_not_invent_characters() -> None:
    """
    Only separators are removed. Were this to start correcting letters it would
    turn a wrong identifier into a different valid one, and the customer would
    be told about somebody else's flight.
    """
    assert _normalise_identifier("AI859") == "AI859"
    assert _normalise_identifier("") == ""


def test_every_registered_tool_has_a_description() -> None:
    """
    Tool descriptions are load-bearing.

    The system prompt was deliberately cut to 591 characters after a longer one
    broke tool calling; the guidance that left it moved into these descriptions
    (ADR 0005). A tool without one is a tool the model cannot choose correctly.
    """
    for name in registry.tools:
        tool = registry.get(name)
        assert tool.description and len(tool.description) > 20, f"{name} needs a real description"


def test_mutating_tools_are_marked_as_such() -> None:
    """
    The confirmation gate keys off `mutating`. A state-changing tool that
    forgets the flag bypasses the gate entirely — silently, and only for that
    one tool.
    """
    expected_mutating = {"cancel_booking", "reschedule_booking"}

    actual = {name for name in registry.tools if registry.get(name).mutating}

    assert actual == expected_mutating


def test_every_shape_validated_names_at_least_one_key() -> None:
    """
    An empty expected-key set makes `issubset` vacuously true, so the malformed
    response check would pass anything at all — including the corrupt bodies the
    chaos engine injects.
    """
    for shape, keys in _EXPECTED_KEYS.items():
        assert keys, f"shape '{shape}' validates nothing"
