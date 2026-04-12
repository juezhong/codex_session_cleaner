# Copyright 2026 liyunfeng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from codex_session_cleaner.models import ConversationRound, SessionParseResult, SessionRecord


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

    records: list[SessionRecord] = []
    for path in _iter_rollup_files(root):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        try:
            records.append(parse_session_file(path).record)
        except OSError:
            continue
    records.sort(key=lambda record: str(record.jsonl_path))
    records.sort(key=lambda record: record.updated_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return records


def _iter_rollup_files(root: Path) -> Iterator[Path]:
    def _onerror(exc: OSError) -> None:
        return None

    for dirpath, _, filenames in os.walk(root, onerror=_onerror):
        for filename in filenames:
            if filename.startswith("rollout-") and filename.endswith(".jsonl"):
                yield Path(dirpath) / filename


def parse_session_file(path: Path) -> SessionParseResult:
    warnings: list[str] = []
    session_id: str | None = None
    cwd: str | None = None
    session_kind = "main"
    session_label: str | None = None
    rollout_messages: list[tuple[str, str]] = []
    event_timestamps: list[datetime] = []
    saw_valid_json_object = False

    try:
        with path.open("rb") as handle:
            for line_number, raw_raw in enumerate(handle, start=1):
                try:
                    raw_line = raw_raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    warnings.append(f"line {line_number}: invalid UTF-8: {exc}")
                    raw_line = raw_raw.decode("utf-8", errors="replace")
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
                if kind_candidate == "subagent":
                    session_kind = "subagent"
                elif session_kind != "subagent" and kind_candidate is not None:
                    session_kind = kind_candidate
                if label_candidate is not None:
                    session_label = label_candidate

                rollout_messages.extend(_extract_rollout_messages(parsed))
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

    conversation_rounds = _build_conversation_rounds(session_id, rollout_messages)
    record = SessionRecord(
        session_id=session_id or "unknown",
        cwd=cwd,
        jsonl_path=path,
        created_at=created_at,
        updated_at=updated_at,
        display_label=_build_display_label(cwd, session_id),
        session_kind=session_kind,
        session_label=session_label,
        conversation_rounds=conversation_rounds,
        warnings=warnings,
    )
    return SessionParseResult(record=record)


def _build_display_label(cwd: str | None, session_id: str | None) -> str:
    short_session_id = _short_session_id(session_id or "unknown")
    cwd_label = cwd or "unknown"
    return f"{cwd_label} · {short_session_id}"


def _build_conversation_rounds(session_id: str | None, rollout_messages: list[tuple[str, str]]) -> tuple[ConversationRound, ...]:
    rounds = _rounds_from_rollout_messages(rollout_messages)
    if rounds:
        return tuple(rounds)
    history_rounds = _rounds_from_history(session_id)
    return tuple(history_rounds)


def _rounds_from_rollout_messages(rollout_messages: list[tuple[str, str]]) -> list[ConversationRound]:
    rounds: list[ConversationRound] = []
    current_user: str | None = None
    assistant_messages: list[str] = []
    for role, text in rollout_messages:
        if role == "user":
            if current_user is not None:
                rounds.append(
                    ConversationRound(
                        user_text=current_user,
                        assistant_text=_combine_assistant_messages(assistant_messages),
                    )
                )
            current_user = text
            assistant_messages = []
        elif role == "assistant":
            if current_user is None:
                continue
            assistant_messages.append(text)
    if current_user is not None:
        rounds.append(
            ConversationRound(
                user_text=current_user,
                assistant_text=_combine_assistant_messages(assistant_messages),
            )
        )
    return rounds


def _combine_assistant_messages(messages: list[str]) -> str | None:
    if not messages:
        return None
    return "\n\n".join(messages)


def _rounds_from_history(session_id: str | None) -> list[ConversationRound]:
    return [ConversationRound(user_text=snippet) for snippet in _history_messages_for_session(session_id)]


def _history_messages_for_session(session_id: str | None) -> list[str]:
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
        with history_path.open("rb") as handle:
            for raw_raw in handle:
                try:
                    line = raw_raw.decode("utf-8")
                except UnicodeDecodeError:
                    line = raw_raw.decode("utf-8", errors="replace")
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
                cleaned = _clean_message_text(text)
                if cleaned is None:
                    continue
                entries_by_session.setdefault(session_id, []).append((timestamp, cleaned))
    except OSError:
        return {}

    for entries in entries_by_session.values():
        entries.sort(key=lambda item: item[0])
    return entries_by_session


def clear_history_cache() -> None:
    _history_entries_by_session.cache_clear()


def _get_history_path() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "history.jsonl"
    home = Path(os.environ.get("HOME", str(Path.home())))
    return home / ".codex" / "history.jsonl"


def _parse_history_timestamp(value: int | float | str) -> int | None:
    if isinstance(value, (int, float)):
        try:
            numeric = float(value)
        except (ValueError, OverflowError):
            return None
        if not math.isfinite(numeric):
            return None
        return int(numeric)
    stripped = value.strip()
    if not stripped:
        return None
    try:
        numeric = float(stripped)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(numeric):
        return None
    return int(numeric)


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


def _extract_rollout_messages(parsed: dict[str, Any]) -> list[tuple[str, str]]:
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

    cleaned = _clean_message_text(" ".join(text_parts))
    if cleaned is None:
        return []
    return [(role, cleaned)]


def _clean_message_text(text: str) -> str | None:
    cleaned = " ".join(text.split())
    if not cleaned:
        return None
    return cleaned


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
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        if _is_numeric_string(stripped):
            try:
                return datetime.fromtimestamp(float(stripped), tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                return None
        normalized = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
        try:
            parsed = datetime.fromisoformat(normalized)
        except (ValueError, OverflowError):
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _is_numeric_string(value: str) -> bool:
    if not value:
        return False
    if value[0] in "+-":
        value = value[1:]
    if not value:
        return False
    dot_seen = False
    digits_seen = False
    for char in value:
        if char == ".":
            if dot_seen:
                return False
            dot_seen = True
            continue
        if not char.isdigit():
            return False
        digits_seen = True
    return digits_seen
