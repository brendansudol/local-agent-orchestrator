from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import CommandResult


PLACEHOLDER_SUMMARY = "No agent summary was captured. See raw command logs."
ASANA_SUMMARY_LIMIT = 3000

_SUMMARY_FIELD_NAMES = (
    "final_message",
    "summary",
    "final",
    "result",
    "message",
    "text",
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_AGENT_SUMMARY_HEADING_RE = re.compile(
    r"(?im)^(#{1,6})\s*Agent Summary\s*:?\s*$"
)
_FENCED_BLOCK_RE = re.compile(
    r"\A```[A-Za-z0-9_-]*\s*\n(?P<body>.*)\n```\s*\Z",
    re.DOTALL,
)


def capture_agent_summary(
    logs_dir: Path,
    result: CommandResult,
    *,
    previous_final_message_mtime_ns: int | None = None,
    preserve_existing: bool = False,
) -> str:
    summary = extract_agent_summary(
        logs_dir,
        result,
        previous_final_message_mtime_ns=previous_final_message_mtime_ns,
    )
    if summary is None and preserve_existing:
        existing = read_captured_agent_summary(logs_dir)
        if existing:
            return existing
    summary = summary or PLACEHOLDER_SUMMARY
    normalized = normalize_agent_summary(summary)
    (logs_dir / "agent_summary.md").write_text(normalized + "\n", encoding="utf-8")
    return normalized


def read_captured_agent_summary(logs_dir: Path) -> str:
    path = logs_dir / "agent_summary.md"
    if not path.exists():
        return ""
    return _read_text(path)


def extract_agent_summary(
    logs_dir: Path,
    result: CommandResult,
    *,
    previous_final_message_mtime_ns: int | None = None,
) -> str | None:
    final_message = logs_dir / "final.md"
    if final_message.exists():
        stat = final_message.stat()
        content = _read_text(final_message)
        if content and (
            previous_final_message_mtime_ns is None
            or stat.st_mtime_ns != previous_final_message_mtime_ns
        ):
            return content

    return extract_summary_from_stdout(result.stdout)


def extract_summary_from_stdout(stdout: str) -> str | None:
    text = _clean_text(stdout)
    if not text:
        return None

    json_summary = _extract_json_summary(text)
    if json_summary:
        return json_summary

    if _parse_json_text(text) is not None:
        return None

    if _looks_like_json_stream(text):
        return None

    heading_summary = _extract_agent_summary_section(text)
    if heading_summary:
        return heading_summary

    if _looks_like_readable_final_message(text):
        return text

    return None


def normalize_agent_summary(summary: str) -> str:
    text = _clean_text(summary) or PLACEHOLDER_SUMMARY
    text = _unwrap_fenced_block(text)

    nested = _extract_json_summary(text)
    if nested:
        text = _clean_text(nested) or PLACEHOLDER_SUMMARY
        text = _unwrap_fenced_block(text)

    agent_summary = _extract_agent_summary_section(text)
    if agent_summary:
        text = agent_summary

    heading = _AGENT_SUMMARY_HEADING_RE.search(text)
    if heading:
        body = text[heading.end() :].strip()
    else:
        body = text.strip()

    if not body:
        body = PLACEHOLDER_SUMMARY
    return f"## Agent Summary\n\n{body}"


def trim_agent_summary_for_asana(summary: str, limit: int = ASANA_SUMMARY_LIMIT) -> str:
    text = summary.strip()
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit].rstrip() + f"\n\n[agent summary truncated {omitted} characters]"


def _extract_json_summary(text: str) -> str | None:
    direct = _parse_json_text(text)
    if direct is not None:
        summary = _extract_from_json_value(direct, allow_plain_string=isinstance(direct, str))
        if summary:
            return summary

    candidates: list[str] = []
    saw_json_line = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        value = _parse_json_text(stripped)
        if value is None:
            continue
        saw_json_line = True
        summary = _extract_from_json_value(value)
        if summary:
            candidates.append(summary)
    if candidates:
        return candidates[-1]
    if saw_json_line:
        return None
    return None


def _extract_from_json_value(value: Any, *, allow_plain_string: bool = False) -> str | None:
    if isinstance(value, dict):
        for field_name in _SUMMARY_FIELD_NAMES:
            if field_name not in value:
                continue
            summary = _extract_from_json_value(
                value[field_name],
                allow_plain_string=True,
            )
            if summary:
                return summary
        for nested in reversed(list(value.values())):
            summary = _extract_from_json_value(nested)
            if summary:
                return summary
        return None

    if isinstance(value, list):
        for nested in reversed(value):
            summary = _extract_from_json_value(nested)
            if summary:
                return summary
        return None

    if isinstance(value, str):
        if not allow_plain_string:
            return None
        text = _clean_text(value)
        if not text:
            return None
        text = _unwrap_fenced_block(text)
        nested = _parse_json_text(text)
        if nested is not None:
            return _extract_from_json_value(nested)
        return text

    return None


def _parse_json_text(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _looks_like_json_stream(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    parsed_count = sum(1 for line in lines if _parse_json_text(line) is not None)
    return parsed_count == len(lines) or (
        parsed_count >= 2 and parsed_count >= len(lines) * 0.8
    )


def _extract_agent_summary_section(text: str) -> str | None:
    matches = list(_AGENT_SUMMARY_HEADING_RE.finditer(text))
    if not matches:
        return None
    return text[matches[-1].start() :].strip()


def _looks_like_readable_final_message(text: str) -> bool:
    if len(text) > 6000:
        return False
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) > 120:
        return False
    punctuation = sum(text.count(char) for char in "{}[]")
    return punctuation < max(20, len(text) // 10)


def _clean_text(value: str) -> str:
    text = _ANSI_ESCAPE_RE.sub("", value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    return text.strip()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def _unwrap_fenced_block(text: str) -> str:
    match = _FENCED_BLOCK_RE.match(text.strip())
    if not match:
        return text.strip()
    return match.group("body").strip()
