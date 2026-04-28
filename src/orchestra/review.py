from __future__ import annotations

from json import JSONDecodeError, JSONDecoder
from typing import Any
import json
import re

from .models import ReviewVerdict


class ReviewParseError(ValueError):
    pass


def parse_review_verdict(output: str) -> ReviewVerdict:
    for candidate in _candidate_objects(output):
        verdict = _coerce_verdict(candidate)
        if verdict:
            return verdict
    raise ReviewParseError("Review output did not contain a JSON verdict")


def _candidate_objects(output: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    stripped = output.strip()
    if stripped:
        parsed = _loads_dict(stripped)
        if parsed:
            candidates.append(parsed)

    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        parsed = _loads_dict(line)
        if parsed:
            candidates.append(parsed)

    for match in reversed(list(re.finditer(r"```(?:json)?\s*(.*?)```", output, re.DOTALL))):
        parsed = _loads_dict(match.group(1).strip())
        if parsed:
            candidates.append(parsed)

    decoder = JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(output[index:])
        except JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidates.append(parsed)

    expanded: list[dict[str, Any]] = []
    for candidate in candidates:
        expanded.append(candidate)
        for key in (
            "result",
            "message",
            "text",
            "summary",
            "content",
            "output",
            "final",
            "final_message",
        ):
            value = candidate.get(key)
            if isinstance(value, str):
                expanded.extend(_candidate_objects(value))
        content = candidate.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    expanded.append(item)
                    text = item.get("text")
                    if isinstance(text, str):
                        expanded.extend(_candidate_objects(text))
                elif isinstance(item, str):
                    expanded.extend(_candidate_objects(item))
    return expanded


def _loads_dict(value: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(value)
    except JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_verdict(value: dict[str, Any]) -> ReviewVerdict | None:
    verdict = value.get("verdict")
    if not isinstance(verdict, str):
        return None
    normalized = verdict.strip().lower()
    if normalized not in {"ok", "blocked"}:
        raise ReviewParseError(f"Invalid review verdict: {verdict!r}")

    findings = value.get("findings", [])
    if not isinstance(findings, list):
        raise ReviewParseError("Review verdict field 'findings' must be a list")
    normalized_findings = [
        item if isinstance(item, dict) else {"message": str(item)}
        for item in findings
    ]
    return ReviewVerdict(verdict=normalized, findings=normalized_findings, raw=value)
