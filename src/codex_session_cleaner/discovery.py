# Copyright 2026 liyunfeng
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import json
import os
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from codex_session_cleaner.models import SessionParseResult, SessionRecord

ROLLUP_GLOB = "rollout-*.jsonl"


def get_session_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "sessions"
    home = Path(os.environ.get("HOME", str(Path.home())))
    return home / ".codex" / "sessions"


def discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
    root = session_root or get_session_root()
    if not root.exists():
        return []

    records = [parse_session_file(path).record for path in root.rglob(ROLLUP_GLOB) if path.is_file()]
    records.sort(key=lambda record: str(record.jsonl_path))
    records.sort(key=lambda record: record.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return records


def parse_session_file(path: Path) -> SessionParseResult:
    warnings: list[str] = []
    session_id: str | None = None
    cwd: str | None = None
    session_kind = "main"
    session_label: str | None = None
    rollout_snippets: list[str] = []
    event_timestamps: list[datetime] = []
    saw_valid_json_object = False

    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw_line in enumerate(handle, start=1):
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    warnings.append(f"line {line_number}: invalid JSON")
                    continue
                if not isinstance(parsed, dict):
                    warnings.append(f"line {line_number}: JSON value is not an object")
                    continue

                saw_valid_json_object = True
                session_candidate = _extract_session_id(parsed)
                if session_candidate is not None:
                    if session_id is None:
                        session_id = session_candidate
                    elif session_candidate != session_id:
                        warnings.append(
                            f"line {line_number}: session_id {session_candidate!r} disagrees with {session_id!r}"
                        )

                cwd_candidate = _extract_cwd(parsed)
                if cwd_candidate is not None:
                    if cwd is None:
                        cwd = cwd_candidate
                    elif cwd_candidate != cwd:
                        warnings.append(f"line {line_number}: cwd {cwd_candidate!r} disagrees with {cwd!r}")

                kind_candidate, label_candidate = _extract_session_identity(parsed)
                if kind_candidate is not None:
                    session_kind = kind_candidate
                if label_candidate is not None:
                    session_label = label_candidate

                rollout_snippets.extend(_extract_rollout_preview_snippets(parsed))
                event_timestamp = _first_parseable_timestamp(parsed)
                if event_timestamp is not None:
                    event_timestamps.append(event_timestamp)
    except OSError as exc:
        warnings.append(f"failed to read file: {exc}")

    if not saw_valid_json_object:
        warnings.append("no valid JSON objects found")

    if event_timestamps:
        created_at = min(event_timestamps)
        updated_at = max(event_timestamps)
    else:
        try:
            stat = path.stat()
        except OSError as exc:
            created_at = None
            updated_at = None
            warnings.append(f"failed to stat file: {exc}")
        else:
            fallback = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            created_at = fallback
            updated_at = fallback
            warnings.append("no parseable timestamps found; used file mtime")

    conversation_preview = _build_conversation_preview(session_id, rollout_snippets)
    record = SessionRecord(
        session_id=session_id or "unknown",
        cwd=cwd,
        jsonl_path=path,
        created_at=created_at,
        updated_at=updated_at,
        display_label=_build_display_label(cwd, session_id),
        session_kind=session_kind,
        session_label=session_label,
        conversation_preview=conversation_preview,
        warnings=warnings,
    )
    return SessionParseResult(record=record)


def _build_display_label(cwd: str | None, session_id: str | None) -> str:
    short_session_id = _short_session_id(session_id or "unknown")
    cwd_label = cwd or "unknown"
    return f"{cwd_label} · {short_session_id}"


def _build_conversation_preview(session_id: str | None, rollout_snippets: list[str]) -> tuple[str, ...]:
    if rollout_snippets:
        return tuple(rollout_snippets[:15])

    history_snippets = _history_snippets_for_session(session_id)
    if history_snippets:
        return tuple(f"user: {snippet}" for snippet in history_snippets[:15])

    return tuple()


def _history_snippets_for_session(session_id: str | None) -> list[str]:
    if not session_id or session_id == "unknown":
        return []

    entries = _history_entries_by_session(_get_history_path()).get(session_id, [])
    return [snippet for _, snippet in entries]


@lru_cache(maxsize=1)
def _history_entries_by_session(history_path: Path) -> dict[str, list[tuple[int, str]]]:
    if not history_path.exists():
        return {}

    entries_by_session: dict[str, list[tuple[int, str]]] = {}
    try:
        with history_path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue

                session_id = _first_non_empty_string(parsed.get("session_id"))
                text = _first_non_empty_string(parsed.get("text"))
                if session_id is None or text is None:
                    continue
                ts = parsed.get("ts")
                if not isinstance(ts, (int, float, str)):
                    continue
                timestamp = _parse_history_timestamp(ts)
                if timestamp is None:
                    continue
                entries_by_session.setdefault(session_id, []).append((timestamp, _clean_preview_text(text)))
    except OSError:
        return {}

    for entries in entries_by_session.values():
        entries.sort(key=lambda item: item[0])
    return entries_by_session


def _get_history_path() -> Path:
    home = Path(os.environ.get("HOME", str(Path.home())))
    return home / ".codex" / "history.jsonl"


def _parse_history_timestamp(value: int | float | str) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return int(float(stripped))
    except ValueError:
        return None


def _extract_session_id(parsed: dict[str, Any]) -> str | None:
    session_meta = parsed.get("session_meta")
    if isinstance(session_meta, dict):
        payload = session_meta.get("payload")
        if isinstance(payload, dict):
            session_candidate = _first_non_empty_string(payload.get("id"))
            if session_candidate is not None:
                return session_candidate
    payload = parsed.get("payload")
    if isinstance(payload, dict):
        session_candidate = _first_non_empty_string(payload.get("id"))
        if session_candidate is not None:
            return session_candidate
    return _first_non_empty_string(parsed.get("session_id"))


def _extract_cwd(parsed: dict[str, Any]) -> str | None:
    session_meta = parsed.get("session_meta")
    if isinstance(session_meta, dict):
        payload = session_meta.get("payload")
        if isinstance(payload, dict):
            cwd_candidate = _first_non_empty_string(payload.get("cwd"))
            if cwd_candidate is not None:
                return cwd_candidate
    payload = parsed.get("payload")
    if isinstance(payload, dict):
        cwd_candidate = _first_non_empty_string(payload.get("cwd"))
        if cwd_candidate is not None:
            return cwd_candidate
    return _first_non_empty_string(parsed.get("cwd"))


def _extract_rollout_preview_snippets(parsed: dict[str, Any]) -> list[str]:
    if parsed.get("type") != "response_item":
        return []

    payload = parsed.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "message":
        return []

    role = payload.get("role")
    if role not in {"user", "assistant"}:
        return []

    content = payload.get("content")
    if not isinstance(content, list):
        return []

    text_parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        text = _first_non_empty_string(item.get("text"))
        if text is not None:
            text_parts.append(text)

    if not text_parts:
        return []

    cleaned = _clean_preview_text(" ".join(text_parts))
    if cleaned is None:
        return []
    return [f"{role}: {cleaned}"]


def _extract_session_identity(parsed: dict[str, Any]) -> tuple[str | None, str | None]:
    if parsed.get("type") != "session_meta":
        return None, None

    payload = parsed.get("payload")
    if not isinstance(payload, dict):
        return None, None

    source = payload.get("source")
    if isinstance(source, dict) and isinstance(source.get("subagent"), dict):
        label = _first_non_empty_string(payload.get("agent_nickname")) or _first_non_empty_string(payload.get("agent_role"))
        return "subagent", label

    return "main", None


def _clean_preview_text(text: str) -> str | None:
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    if len(cleaned) > 180:
        cleaned = cleaned[:177].rstrip() + "..."
    return cleaned


def _short_session_id(session_id: str) -> str:
    if session_id == "unknown":
        return session_id
    return session_id[:8]


def _first_non_empty_string(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _first_parseable_timestamp(obj: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "created_at", "updated_at", "time"):
        if key not in obj:
            continue
        timestamp = _parse_timestamp_value(obj[key])
        if timestamp is not None:
            return timestamp
    return None


def _parse_timestamp_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return datetime.fromtimestamp(float(stripped), tz=timezone.utc)
        normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None
