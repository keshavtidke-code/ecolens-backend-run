"""Rule-based suggested questions. Deterministic, free and instant (no Gemini call)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

MAX_SUGGESTIONS = 4


def build_suggestions(
    *,
    ecosystem: str,
    perception: dict[str, Any],
    weather: Optional[dict[str, Any]],
    challenge_title: Optional[str],
    action_title: Optional[str],
    low_confidence: bool,
    already_asked: Sequence[str] = (),
) -> list[str]:
    asked = {q.strip().lower() for q in already_asked}
    eco = ecosystem if ecosystem and ecosystem != "unclear" else "ecosystem"
    candidates: list[str] = []

    if low_confidence:
        candidates.append("How can I check what kind of ecosystem this really is?")
    candidates.append(f"Why is this {eco} important?" if eco != "ecosystem" else "Why is this ecosystem important?")

    if weather and weather.get("humidity_pct") is not None:
        candidates.append("How does humidity affect this area?")
    elif weather and weather.get("temperature_c") is not None:
        candidates.append("How does the temperature affect this area?")
    else:
        candidates.append("How does weather shape this kind of ecosystem?")

    candidates.append("What animals might live here?")
    candidates.append("What changes should I observe next month?")
    # Context-specific extras; they move up as the user works through the defaults above.
    if perception.get("environmental_pressures"):
        candidates.append("What is putting pressure on this ecosystem?")
    if perception.get("flora"):
        candidates.append("What do the plants here do for the ecosystem?")
    if challenge_title:
        candidates.append(f"What should I look for in the “{challenge_title[:60]}” challenge?")
    if action_title:
        candidates.append(f"How does “{action_title[:60]}” help this place?")

    result: list[str] = []
    for question in candidates:
        if question.lower() not in asked and question not in result:
            result.append(question)
        if len(result) == MAX_SUGGESTIONS:
            break
    return result
