from __future__ import annotations

LEGACY_OUTCOME_ALIASES = {
    "improved_baseline": "met_targets",
    "did_not_improve_baseline": "below_targets",
}


def normalize_research_outcome_name(name: str) -> str:
    return LEGACY_OUTCOME_ALIASES.get(str(name), str(name))


def outcome_met_targets(name: str) -> bool:
    return normalize_research_outcome_name(name) == "met_targets"
