"""Perception-stage output: Pydantic validation + the matching Gemini response schema."""
from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, ValidationError

WaterPresence = Literal[
    "none", "damp_ground", "puddle", "stream", "pond_or_lake", "river",
    "coastal_or_sea", "wetland", "unclear",
]
SoilCondition = Literal[
    "not_visible", "dry", "moist", "waterlogged", "compacted_or_bare",
    "sandy_or_rocky", "paved_over", "rich_organic",
]
VegetationDensity = Literal["none", "sparse", "moderate", "dense", "very_dense"]

MAX_ITEMS = 12
MAX_ITEM_CHARS = 200
MAX_TYPE_CHARS = 100


def _normalize_enum(value: Any) -> Any:
    if isinstance(value, str):
        return re.sub(r"[\s\-/]+", "_", value.strip().lower())
    return value


def _clean_text(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip().lower()[:MAX_TYPE_CHARS]
    return value


def _clean_list(value: Any) -> Any:
    """Trim, drop blanks, de-duplicate (case-insensitive), cap length. Non-lists/non-strings are left to fail."""
    if not isinstance(value, list):
        return value
    cleaned: list[Any] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            return value  # let Pydantic report the wrong type
        text = item.strip()[:MAX_ITEM_CHARS]
        if text and text.lower() not in seen:
            seen.add(text.lower())
            cleaned.append(text)
    return cleaned[:MAX_ITEMS]


StrList = Annotated[list[str], BeforeValidator(_clean_list)]


class PerceptionResult(BaseModel):
    """What Gemini saw. Every field is required; unknown extra keys are ignored."""

    model_config = ConfigDict(extra="ignore")

    ecosystem_type: Annotated[str, BeforeValidator(_clean_text)] = Field(min_length=1, max_length=MAX_TYPE_CHARS)
    ecosystem_confidence: float = Field(ge=0.0, le=1.0)
    visible_elements: StrList
    flora: StrList
    fauna: StrList
    water_presence: Annotated[WaterPresence, BeforeValidator(_normalize_enum)]
    soil_condition: Annotated[SoilCondition, BeforeValidator(_normalize_enum)]
    vegetation_density: Annotated[VegetationDensity, BeforeValidator(_normalize_enum)]
    human_impact: StrList
    environmental_pressures: StrList
    interesting_observations: StrList
    confidence: float = Field(ge=0.0, le=1.0)


class PerceptionParseError(ValueError):
    """Gemini's reply was not usable JSON for the schema. `reason` is safe to show the model."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def parse_perception(raw: str | None) -> PerceptionResult:
    text = (raw or "").strip()
    fenced = _FENCE.match(text)
    if fenced:  # models occasionally wrap JSON in a markdown fence despite instructions
        text = fenced.group(1).strip()
    if not text:
        raise PerceptionParseError("the reply was empty")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PerceptionParseError(f"the reply was not valid JSON ({exc.msg} at position {exc.pos})") from None
    if not isinstance(data, dict):
        raise PerceptionParseError("the top-level JSON value must be an object")
    try:
        return PerceptionResult.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(p) for p in err['loc']) or 'root'}: {err['msg']}" for err in exc.errors()[:6]
        ]
        raise PerceptionParseError("schema validation failed - " + "; ".join(problems)[:600]) from None


def gemini_response_schema() -> dict[str, Any]:
    """Gemini `responseSchema` (OpenAPI subset) for PerceptionResult. Pydantic still validates the reply."""

    def array(description: str) -> dict[str, Any]:
        return {"type": "ARRAY", "items": {"type": "STRING"}, "description": description}

    def enum(values: tuple[str, ...], description: str) -> dict[str, Any]:
        return {"type": "STRING", "enum": list(values), "description": description}

    properties: dict[str, Any] = {
        "ecosystem_type": {"type": "STRING", "description": "Short lowercase habitat label, e.g. 'urban park'."},
        "ecosystem_confidence": {"type": "NUMBER", "description": "0.0-1.0 confidence in ecosystem_type."},
        "visible_elements": array("Notable things visible: land cover, structures, sky, objects."),
        "flora": array("Plants and trees actually visible."),
        "fauna": array("Animals or direct signs of animals actually visible."),
        "water_presence": enum(get_args(WaterPresence), "Water visible or clearly evidenced."),
        "soil_condition": enum(get_args(SoilCondition), "Ground/soil condition."),
        "vegetation_density": enum(get_args(VegetationDensity), "Overall plant cover."),
        "human_impact": array("Visible human-made features and effects (buildings, roads, litter...)."),
        "environmental_pressures": array("Stresses on this ecosystem supported by visible evidence."),
        "interesting_observations": array("2-5 specific details worth a closer look."),
        "confidence": {"type": "NUMBER", "description": "0.0-1.0 overall confidence in this analysis."},
    }
    return {
        "type": "OBJECT",
        "properties": properties,
        "required": list(properties),
        "propertyOrdering": list(properties),
    }
