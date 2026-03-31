# Copyright 2026 liyunfeng
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from codex_session_cleaner.discovery import (
    discover_sessions,
    get_session_root,
    parse_session_file,
)


def write_jsonl(path: Path, lines: list[object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            if isinstance(line, str):
                handle.write(line)
            else:
                handle.write(json.dumps(line))
            handle.write("\n")


def test_session_root_prefers_codex_home_over_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert get_session_root() == codex_home / "sessions"


def test_session_root_falls_back_to_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    assert get_session_root() == home / ".codex" / "sessions"


def test_discover_sessions_finds_only_rollout_jsonl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".codex" / "sessions"
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    write_jsonl(root / "2026/03/30/rollout-a.jsonl", [{"session_id": "alpha", "cwd": "/a", "timestamp": "2026-03-30T01:00:00+00:00"}])
    write_jsonl(root / "2026/03/30/not-rollout.jsonl", [{"session_id": "ignored", "cwd": "/x"}])
    write_jsonl(root / "2026/03/30/rollout-b.txt", [{"session_id": "ignored-too", "cwd": "/y"}])
    write_jsonl(root / "2026/03/30/nested/rollout-b.jsonl", [{"session_id": "beta", "cwd": "/b", "timestamp": "2026-03-30T02:00:00+00:00"}])

    records = discover_sessions()

    assert [record.jsonl_path.name for record in records] == ["rollout-b.jsonl", "rollout-a.jsonl"]


def test_discover_sessions_breaks_timestamp_ties_by_path_ascending(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    root = home / ".codex" / "sessions"
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    timestamp = "2026-03-30T01:00:00+00:00"
    write_jsonl(root / "2026/03/30/rollout-b.jsonl", [{"session_id": "b", "cwd": "/b", "timestamp": timestamp}])
    write_jsonl(root / "2026/03/30/rollout-a.jsonl", [{"session_id": "a", "cwd": "/a", "timestamp": timestamp}])

    records = discover_sessions()

    assert [record.jsonl_path.name for record in records] == ["rollout-a.jsonl", "rollout-b.jsonl"]


def test_parse_session_file_uses_first_non_empty_session_id_and_cwd(tmp_path: Path) -> None:
    path = tmp_path / "rollout-a.jsonl"
    write_jsonl(
        path,
        [
            {"session_id": "", "cwd": "", "timestamp": "2026-03-30T01:00:00+00:00"},
            {"session_id": "first-id", "cwd": "", "created_at": "2026-03-30T02:00:00+00:00"},
            {"session_id": "second-id", "cwd": "/work", "time": "2026-03-30T03:00:00+00:00"},
            {"session_id": "third-id", "cwd": "/other", "updated_at": "2026-03-30T04:00:00+00:00"},
        ],
    )

    record = parse_session_file(path).record

    assert record.session_id == "first-id"
    assert record.cwd == "/work"


def test_parse_session_file_collects_timestamps_in_precedence_order(tmp_path: Path) -> None:
    path = tmp_path / "rollout-a.jsonl"
    write_jsonl(
        path,
        [
            {"timestamp": "2026-03-30T04:00:00+00:00"},
            {"created_at": "2026-03-30T02:00:00+00:00"},
            {"updated_at": "2026-03-30T03:00:00+00:00"},
            {"time": "2026-03-30T01:00:00+00:00"},
            {"timestamp": "not-a-timestamp", "created_at": "2026-03-30T05:00:00+00:00"},
        ],
    )

    record = parse_session_file(path).record

    assert record.created_at == datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc)
    assert record.updated_at == datetime(2026, 3, 30, 5, 0, tzinfo=timezone.utc)


def test_parse_session_file_warns_on_later_conflicts(tmp_path: Path) -> None:
    path = tmp_path / "rollout-a.jsonl"
    write_jsonl(
        path,
        [
            {"session_id": "alpha", "cwd": "/one", "timestamp": "2026-03-30T01:00:00+00:00"},
            {"session_id": "beta", "cwd": "/two", "timestamp": "2026-03-30T02:00:00+00:00"},
        ],
    )

    result = parse_session_file(path)

    assert result.record.session_id == "alpha"
    assert result.record.cwd == "/one"
    assert any("session_id" in warning for warning in result.record.warnings)
    assert any("cwd" in warning for warning in result.record.warnings)


def test_parse_session_file_preserves_malformed_files_with_warnings(tmp_path: Path) -> None:
    path = tmp_path / "rollout-a.jsonl"
    write_jsonl(
        path,
        [
            "not-json",
            {"session_id": "", "cwd": "", "timestamp": "still-not-a-timestamp"},
            {"session_id": "", "cwd": ""},
        ],
    )

    result = parse_session_file(path)

    assert result.record.session_id == "unknown"
    assert result.record.cwd is None
    assert result.record.warnings


def test_parse_session_file_surfaces_unreadable_and_unstatable_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "rollout-a.jsonl"
    path.write_text("ignored", encoding="utf-8")

    original_open = Path.open
    original_stat = Path.stat

    def fake_open(self: Path, *args: object, **kwargs: object):
        if self == path:
            raise OSError("permission denied")
        return original_open(self, *args, **kwargs)

    def fake_stat(self: Path, *args: object, **kwargs: object):
        if self == path:
            raise OSError("cannot stat file")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fake_open)
    monkeypatch.setattr(Path, "stat", fake_stat)

    result = parse_session_file(path).record

    assert result.session_id == "unknown"
    assert result.cwd is None
    assert result.created_at is None
    assert result.updated_at is None
    assert any("failed to read file" in warning for warning in result.warnings)
    assert any("failed to stat file" in warning for warning in result.warnings)


def test_parse_session_file_without_session_id_has_defined_display_label(tmp_path: Path) -> None:
    path = tmp_path / "rollout-a.jsonl"
    write_jsonl(
        path,
        [
            {"cwd": "/project", "timestamp": "2026-03-30T01:00:00+00:00"},
        ],
    )

    record = parse_session_file(path).record

    assert record.session_id == "unknown"
    assert record.display_label == "/project · unknown"


def test_parse_session_file_uses_session_meta_payload_and_rollout_preview(tmp_path: Path) -> None:
    path = tmp_path / "rollout-a.jsonl"
    write_jsonl(
        path,
        [
            {
                "timestamp": "2026-03-30T01:00:00+00:00",
                "type": "session_meta",
                "payload": {"id": "session-123", "cwd": "/project"},
            },
            {
                "timestamp": "2026-03-30T01:01:00+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "First question"}],
                },
            },
            {
                "timestamp": "2026-03-30T01:02:00+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "First answer"}],
                },
            },
            {
                "timestamp": "2026-03-30T01:03:00+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Second question"}],
                },
            },
        ],
    )

    record = parse_session_file(path).record

    assert record.session_id == "session-123"
    assert record.cwd == "/project"
    assert record.conversation_preview == (
        "user: First question",
        "assistant: First answer",
        "user: Second question",
    )


def test_parse_session_file_prefers_rollout_preview_and_only_falls_back_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    session_root = home / ".codex" / "sessions"
    history_path = home / ".codex" / "history.jsonl"
    monkeypatch.setenv("HOME", str(home))

    write_jsonl(
        history_path,
        [
            {"session_id": "session-123", "ts": 10, "text": "history one"},
            {"session_id": "session-123", "ts": 20, "text": "history two"},
            {"session_id": "session-123", "ts": 30, "text": "history three"},
            {"session_id": "session-123", "ts": 40, "text": "history four"},
        ],
    )
    write_jsonl(
        session_root / "2026/03/30/rollout-a.jsonl",
        [
            {
                "timestamp": "2026-03-30T01:00:00+00:00",
                "type": "session_meta",
                "payload": {"id": "session-123", "cwd": "/project"},
            },
            {
                "timestamp": "2026-03-30T01:01:00+00:00",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Only rollout snippet"}],
                },
            },
        ],
    )

    record = parse_session_file(session_root / "2026/03/30/rollout-a.jsonl").record

    assert record.conversation_preview == (
        "user: Only rollout snippet",
    )
