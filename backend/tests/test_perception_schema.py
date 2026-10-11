import json
from typing import get_args

import pytest

from app.ai.schemas import (
    MAX_ITEMS,
    PerceptionParseError,
    PerceptionResult,
    SoilCondition,
    VegetationDensity,
    WaterPresence,
    gemini_response_schema,
    parse_perception,
)
from tests.conftest import VALID_PERCEPTION

SPEC_FIELDS = [
    "ecosystem_type", "ecosystem_confidence", "visible_elements", "flora", "fauna",
    "water_presence", "soil_condition", "vegetation_density", "human_impact",
    "environmental_pressures", "interesting_observations", "confidence",
]


def raw(**overrides):
    return json.dumps({**VALID_PERCEPTION, **overrides})


def test_valid_payload_parses_and_dumps_the_spec_fields_in_order():
    result = parse_perception(raw())
    assert list(result.model_dump()) == SPEC_FIELDS
    assert result.ecosystem_type == "urban park" and result.water_presence == "damp_ground"


def test_markdown_fenced_json_is_accepted():
    assert parse_perception(f"```json\n{raw()}\n```").confidence == 0.82


def test_extra_keys_are_ignored():
    assert "bonus" not in parse_perception(raw(bonus="x")).model_dump()


def test_normalisation_trims_dedupes_and_caps_lists():
    many = [f"item {i}" for i in range(30)]
    result = parse_perception(raw(flora=["  Fern ", "fern", "", "FERN"], fauna=many, ecosystem_type="  Urban Park "))
    assert result.flora == ["Fern"]
    assert len(result.fauna) == MAX_ITEMS
    assert result.ecosystem_type == "urban park"


def test_enum_values_are_normalised():
    result = parse_perception(raw(water_presence="Pond or Lake", soil_condition="Compacted-or-Bare", vegetation_density=" Very Dense"))
    assert (result.water_presence, result.soil_condition, result.vegetation_density) == (
        "pond_or_lake", "compacted_or_bare", "very_dense",
    )


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "not json at all",
        '{"ecosystem_type": "x"',            # truncated
        "[1, 2, 3]",                          # wrong top-level type
        raw(confidence=1.5),                  # out of range
        raw(ecosystem_confidence=-0.1),
        raw(confidence="high"),
        raw(water_presence="swimming pool"),  # not in enum
        raw(flora="oak"),                     # string instead of list
        raw(flora=["oak", 7]),                # non-string item
        raw(ecosystem_type=""),
    ],
    ids=["empty", "blank", "prose", "truncated", "array", "conf>1", "conf<0", "conf-text",
         "bad-enum", "list-as-str", "non-str-item", "empty-type"],
)
def test_invalid_payloads_raise_parse_error(text):
    with pytest.raises(PerceptionParseError) as exc:
        parse_perception(text)
    assert exc.value.reason


@pytest.mark.parametrize("field", SPEC_FIELDS)
def test_every_field_is_required(field):
    data = dict(VALID_PERCEPTION)
    del data[field]
    with pytest.raises(PerceptionParseError) as exc:
        parse_perception(json.dumps(data))
    assert field in exc.value.reason


def test_gemini_schema_matches_the_pydantic_model():
    schema = gemini_response_schema()
    assert schema["type"] == "OBJECT"
    assert schema["required"] == schema["propertyOrdering"] == SPEC_FIELDS
    assert set(schema["properties"]) == set(PerceptionResult.model_fields)
    props = schema["properties"]
    assert props["water_presence"]["enum"] == list(get_args(WaterPresence))
    assert props["soil_condition"]["enum"] == list(get_args(SoilCondition))
    assert props["vegetation_density"]["enum"] == list(get_args(VegetationDensity))
    assert props["flora"] == {**props["flora"], "type": "ARRAY", "items": {"type": "STRING"}}
