from __future__ import annotations

import re
from typing import Any

from evalkit.models import EvalCase, RubricDimension


def run_logic_check(case: EvalCase, dimension: RubricDimension) -> tuple[bool, str, dict[str, Any]]:
    value = _target_value(case, dimension)
    rule = dimension.rule
    if not rule:
        raise ValueError(f"Logic dimension '{dimension.name}' is missing a rule.")

    if rule == "max_chars":
        actual = len(value)
        threshold = _required_threshold(dimension)
        return actual <= threshold, f"{actual} characters; max is {threshold}.", {"actual": actual}

    if rule == "min_chars":
        actual = len(value)
        threshold = _required_threshold(dimension)
        return actual >= threshold, f"{actual} characters; min is {threshold}.", {"actual": actual}

    if rule == "contains_cta":
        cta_terms = dimension.options.get("terms") or ["learn more", "get started", "sign up", "try", "book", "download"]
        matched = [term for term in cta_terms if term.lower() in value.lower()]
        return bool(matched), f"CTA terms matched: {', '.join(matched) if matched else 'none'}.", {"matched": matched}

    if rule == "required_terms":
        terms = dimension.options.get("terms") or []
        missing = [term for term in terms if term.lower() not in value.lower()]
        return not missing, f"Missing required terms: {', '.join(missing) if missing else 'none'}.", {"missing": missing}

    if rule == "forbidden_terms":
        terms = dimension.options.get("terms") or []
        found = [term for term in terms if term.lower() in value.lower()]
        return not found, f"Forbidden terms found: {', '.join(found) if found else 'none'}.", {"found": found}

    if rule == "regex":
        pattern = dimension.options.get("pattern")
        if not pattern:
            raise ValueError(f"Regex dimension '{dimension.name}' is missing pattern.")
        matched = bool(re.search(pattern, value, flags=re.IGNORECASE))
        return matched, f"Regex matched: {matched}.", {"pattern": pattern}

    raise ValueError(f"Unsupported logic rule '{rule}'.")


def _target_value(case: EvalCase, dimension: RubricDimension) -> str:
    if dimension.field:
        value = case.artifact.fields.get(dimension.field, "")
        return "" if value is None else str(value)
    return case.artifact.content


def _required_threshold(dimension: RubricDimension) -> int:
    if dimension.threshold is None:
        raise ValueError(f"Logic dimension '{dimension.name}' is missing threshold.")
    return int(dimension.threshold)
