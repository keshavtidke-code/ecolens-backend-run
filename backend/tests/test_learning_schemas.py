import copy
import json
from typing import get_args

import pytest

from app.ai.learning_schemas import (
    ActionCategory,
    ImpactLevel,
    LearningParseError,
    learning_response_schema,
    parse_learning_experience,
)
from app.ai.safety import find_unsafe_phrases
from tests.conftest import VALID_LEARNING


def raw(mutate=None):
    data = copy.deepcopy(VALID_LEARNING)
    if mutate:
        mutate(data)
    return json.dumps(data)


def test_valid_payload_parses_with_the_spec_shape():
    result = parse_learning_experience(raw())
    dumped = result.model_dump()
    assert list(dumped) == ["lesson", "challenge", "recommended_action"]
    assert list(dumped["lesson"]) == [
        "title", "ecosystem_summary", "what_you_are_seeing", "why_it_matters",
        "weather_connection", "interesting_fact", "key_concepts",
    ]
    assert list(dumped["challenge"]) == ["title", "description", "instructions", "estimated_minutes", "success_question"]
    assert list(dumped["recommended_action"]) == ["title", "description", "category", "impact_level"]


def test_markdown_fence_is_accepted_and_whitespace_collapsed():
    def m(d):
        d["lesson"]["title"] = "  Why   Trees\nMatter "

    result = parse_learning_experience(f"```json\n{raw(m)}\n```")
    assert result.lesson.title == "Why Trees Matter"


def test_enum_values_are_normalised():
    def m(d):
        d["recommended_action"].update(category="Waste Reduction", impact_level=" HIGH ")

    result = parse_learning_experience(raw(m))
    assert (result.recommended_action.category, result.recommended_action.impact_level) == ("waste_reduction", "high")


def test_key_concepts_are_deduped_and_capped():
    def m(d):
        d["lesson"]["key_concepts"] = ["Shade", "shade", "heat", "a", "b", "c", "d"]

    assert parse_learning_experience(raw(m)).lesson.key_concepts == ["Shade", "heat", "a", "b", "c"]


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda d: d.pop("challenge"), "challenge"),
        (lambda d: d["lesson"].pop("weather_connection"), "weather_connection"),
        (lambda d: d["challenge"].pop("success_question"), "success_question"),
        (lambda d: d["recommended_action"].pop("impact_level"), "impact_level"),
        (lambda d: d.update(bonus="x"), "bonus"),                                   # strict: no extra keys
        (lambda d: d["lesson"].update(bonus="x"), "bonus"),
        (lambda d: d["lesson"].update(title=""), "title"),
        (lambda d: d["lesson"].update(key_concepts=["only one"]), "key_concepts"),
        (lambda d: d["challenge"].update(instructions=["just one"]), "instructions"),
        (lambda d: d["challenge"].update(estimated_minutes=0), "estimated_minutes"),
        (lambda d: d["challenge"].update(estimated_minutes=60), "estimated_minutes"),
        (lambda d: d["challenge"].update(estimated_minutes="five"), "estimated_minutes"),
        (lambda d: d["recommended_action"].update(category="world peace"), "category"),
        (lambda d: d["recommended_action"].update(impact_level="huge"), "impact_level"),
    ],
)
def test_invalid_payloads_are_rejected_with_a_useful_reason(mutate, fragment):
    with pytest.raises(LearningParseError) as exc:
        parse_learning_experience(raw(mutate))
    assert fragment in exc.value.reason


@pytest.mark.parametrize("text", ["", "   ", "not json", '{"lesson": {', "[1, 2]"])
def test_unparseable_replies(text):
    with pytest.raises(LearningParseError):
        parse_learning_experience(text)


def test_lesson_length_is_bounded_for_a_45_to_90_second_read():
    def too_short(d):
        for f in ("ecosystem_summary", "what_you_are_seeing", "why_it_matters", "weather_connection", "interesting_fact"):
            d["lesson"][f] = "Short."

    def too_long(d):
        d["lesson"]["what_you_are_seeing"] = ("word " * 120).strip()
        d["lesson"]["why_it_matters"] = ("word " * 120).strip()
        d["lesson"]["weather_connection"] = ("word " * 120).strip()

    for mutate in (too_short, too_long):
        with pytest.raises(LearningParseError) as exc:
            parse_learning_experience(raw(mutate))
        assert "words" in exc.value.reason


def test_unsafe_challenge_is_rejected():
    def m(d):
        d["challenge"]["instructions"][0] = "Catch a butterfly and look at its wings."

    with pytest.raises(LearningParseError) as exc:
        parse_learning_experience(raw(m))
    assert "safety check failed" in exc.value.reason and "animals" in exc.value.reason


def test_unsafe_action_is_rejected():
    def m(d):
        d["recommended_action"]["description"] = "Pick up the litter you see around the park."

    with pytest.raises(LearningParseError) as exc:
        parse_learning_experience(raw(m))
    assert "safety check failed" in exc.value.reason


# ------------------------------ safety guardrails ------------------------------
UNSAFE = [
    "Touch the bark and then pet the squirrel.",
    "Catch a butterfly and look at it.",
    "Feed the birds some bread.",
    "Taste a berry from the bush.",
    "Eat a mushroom you find to see how it tastes.",
    "Step into the pond to cool your feet.",
    "Wade into the stream and look for fish.",
    "Dip your hand in the water.",
    "Climb the tree for a better view.",
    "Climb over the wall to get closer.",
    "Pick up the litter you see.",
    "Handle the broken glass carefully.",
    "Collect the trash near the bin.",
    "Walk into the road to compare shade.",
    "Pick a flower to examine it.",
    "Enter the fenced area behind the building.",
    "Stand on the ledge to see further.",
]
SAFE = [
    "Do not touch the insects; watch from a distance.",
    "Observe the water edge from a safe distance.",
    "Stay on the path and avoid entering the water.",
    "Look at the flowers but don't pick them.",
    "Photograph litter without touching it.",
    "Count the bees visiting one flowering plant for two minutes.",
    "Pick up your phone and take a photo of the shade.",
    "Notice how the shade feels cooler.",
    "Walk along the path and compare sun and shade.",
    "Never feed or approach wildlife.",
    "Stay out of restricted areas and stay on public paths.",
    "Find one vegetated area and one paved area and compare their shade.",
    "Drink some water before you start, and stay in the shade.",
]


@pytest.mark.parametrize("text", UNSAFE)
def test_unsafe_instructions_are_flagged(text):
    assert find_unsafe_phrases(text), text


@pytest.mark.parametrize("text", SAFE)
def test_safe_instructions_pass(text):
    assert find_unsafe_phrases(text) == [], text


def test_negation_is_judged_per_clause():
    assert find_unsafe_phrases("Do not run, and catch a frog.")  # the second clause is still unsafe
    assert find_unsafe_phrases("Look closely, but do not touch the frog.") == []


# ------------------------------ Gemini schema ------------------------------
def test_gemini_schema_mirrors_the_models():
    schema = learning_response_schema()
    assert schema["required"] == schema["propertyOrdering"] == ["lesson", "challenge", "recommended_action"]
    for name, model in (("lesson", "LessonResult"), ("challenge", "ChallengeResult"), ("recommended_action", "RecommendedActionResult")):
        from app.ai import learning_schemas

        section = schema["properties"][name]
        assert section["type"] == "OBJECT"
        assert section["required"] == section["propertyOrdering"] == list(getattr(learning_schemas, model).model_fields)
    action = schema["properties"]["recommended_action"]["properties"]
    assert action["category"]["enum"] == list(get_args(ActionCategory))
    assert action["impact_level"]["enum"] == list(get_args(ImpactLevel))
    assert schema["properties"]["challenge"]["properties"]["estimated_minutes"]["type"] == "INTEGER"
