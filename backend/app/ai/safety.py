"""Output guardrails for AI-generated challenges and actions.

The prompt tells Gemini what is forbidden; this is the code-level backstop. It flags imperative
text that asks the user to touch/catch animals, taste plants, enter water, climb, trespass, handle
garbage/chemicals, damage plants or step into traffic. Sentences that *forbid* the behaviour
("do not touch the bird", "without entering the water") are fine and are not flagged.

It is deliberately conservative: a false positive only costs one corrective retry.
"""
from __future__ import annotations

import re

_ANIMALS = (
    r"(?:animals?|wildlife|birds?|insects?|bees?|wasps?|snakes?|frogs?|toads?|lizards?|spiders?|"
    r"butterfl(?:y|ies)|bugs?|squirrels?|dogs?|cats?|monkeys?|caterpillars?|fish|crabs?|nests?|eggs?)"
)
_EDIBLE = r"(?:plants?|leaf|leaves|berr(?:y|ies)|fruits?|mushrooms?|fungi|flowers?|seeds?|nuts?|herbs?|roots?|sap)"
_WATER = r"(?:water|ponds?|lakes?|rivers?|streams?|sea|ocean|canals?|drains?|puddles?|waves?|surf|creeks?)"
_WASTE = (
    r"(?:garbage|litter|trash|waste|rubbish|chemicals?|syringes?|needles?|bins?|dumps?|sewage|oil|"
    r"paint|pesticides?|fertili[sz]ers?|batter(?:y|ies)|glass|debris)"
)
_PLANTS = r"(?:flowers?|plants?|leaves|branch(?:es)?|saplings?|seedlings?|fruits?|berr(?:y|ies)|mushrooms?)"

_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "touching, catching or disturbing animals",
        re.compile(
            rf"\b(?:touch|pet|feed|chase|catch|capture|trap|handle|hold|pick up|grab|disturb|poke|approach)\b"
            rf"[^.]{{0,40}}\b{_ANIMALS}\b",
            re.I,
        ),
    ),
    (
        "tasting or eating plants or mushrooms",
        re.compile(
            rf"\b(?:taste|eat|ingest|chew|lick|sip|sample|forage for|consume)\b[^.]{{0,40}}\b{_EDIBLE}\b", re.I
        ),
    ),
    (
        "entering water",
        re.compile(
            rf"\b(?:enter|step into|step in|wade|swim|dive|jump into|get into|go into|walk into|dip|paddle|splash|bathe)\b"
            rf"[^.]{{0,30}}\b{_WATER}\b",
            re.I,
        ),
    ),
    ("climbing", re.compile(r"\bclimb(?:ing)?\b", re.I)),
    (
        "standing at dangerous heights",
        re.compile(
            r"\b(?:lean|hang|stand|sit)\b[^.]{0,20}\b(?:over|on)\b[^.]{0,20}\b(?:ledge|cliff|roof|rooftop|railing|parapet)\b",
            re.I,
        ),
    ),
    (
        "entering restricted or private areas",
        re.compile(
            r"\b(?:enter|go into|go inside|cross|jump over|climb over|sneak into|step onto)\b"
            r"[^.]{0,40}\b(?:private|restricted|fenced|gated|construction site|off-limits|closed|locked|someone's|neighbou?r's)\b"
            r"|\btrespass",
            re.I,
        ),
    ),
    (
        "handling garbage or chemicals",
        re.compile(
            rf"\b(?:touch|pick up|pick|handle|collect|move|sort|open|dig|sift|rummage|pour|smell|sniff|clean up|"
            rf"remove|throw away|dispose of)\b[^.]{{0,40}}\b{_WASTE}\b",
            re.I,
        ),
    ),
    (
        "damaging or removing plants",
        re.compile(
            rf"\b(?:pick(?! up)|pluck|uproot|dig up|cut|break off|tear)\b[^.]{{0,30}}\b{_PLANTS}\b", re.I
        ),
    ),
    (
        "stepping into traffic",
        re.compile(
            r"\b(?:step|walk|stand|run|cross|sit|lie|lean)\b[^.]{0,15}\b(?:onto|into|on|in|across|over)\b[^.]{0,10}"
            r"\b(?:the )?(?:road|roadway|street|highway|traffic|railway|railroad|tracks?|junction)\b",
            re.I,
        ),
    ),
]

_NEGATION = re.compile(
    r"\b(?:don't|do not|dont|never|avoid|avoiding|without|not|instead of|rather than|refrain from|"
    r"no need to|stay away from|keep away from|steer clear of)\b",
    re.I,
)
# Split into clauses so "Look, but don't touch the bee" is judged clause by clause.
_CLAUSES = re.compile(r"[.!?;\n]+|,\s*(?:but|and|then|so)\b|\bbut\b", re.I)


def find_unsafe_phrases(text: str) -> list[str]:
    """Return human-readable descriptions of unsafe instructions found in `text` (empty = OK)."""
    hits: list[str] = []
    for clause in _CLAUSES.split(text or ""):
        clause = clause.strip()
        if not clause or _NEGATION.search(clause):
            continue
        for label, pattern in _RULES:
            if pattern.search(clause):
                hits.append(f"{label} ('{clause[:100]}')")
                break
    return hits
