# Copyright 2026 liyunfeng
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from codex_session_cleaner.models import SessionRecord
import codex_session_cleaner.trash as trash_module
from codex_session_cleaner.trash import (
    build_trash_path,
    get_trash_root,
    move_record_to_trash,
    prune_empty_session_dirs,
    resolve_collision,
    trash_records,
)


def make_record(path: Path, session_id: str | None = "alpha") -> SessionRecord:
    return SessionRecord(
        session_id=session_id or "",
        cwd="/work",
        jsonl_path=path,
        created_at=datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
        display_label="/work · alpha",
    )


def test_get_trash_root_prefers_home_when_codex_home_unset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    assert get_trash_root() == home / ".codex" / "trash" / "sessions"


def test_get_trash_root_uses_codex_home_when_set(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    assert get_trash_root() == codex_home / "trash" / "sessions"


def test_build_trash_path_preserves_relative_hierarchy(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    record = make_record(session_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl", "alpha")

    assert build_trash_path(record, session_root, trash_root) == trash_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"


def test_resolve_collision_appends_short_session_id_before_jsonl(tmp_path: Path) -> None:
    destination = tmp_path / "trash" / "sessions" / "2026" / "03" / "30" / "rollout-a.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("existing", encoding="utf-8")

    resolved = resolve_collision(destination, "sessionid123")

    assert resolved == destination.with_name("rollout-a.sessioni.jsonl")


def test_resolve_collision_escalates_dup_suffixes(tmp_path: Path) -> None:
    destination = tmp_path / "trash" / "sessions" / "2026" / "03" / "30" / "rollout-a.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("existing", encoding="utf-8")
    first = destination.with_name("rollout-a.sessioni.jsonl")
    first.write_text("existing", encoding="utf-8")
    second = destination.with_name("rollout-a.sessioni.dup1.jsonl")
    second.write_text("existing", encoding="utf-8")

    resolved = resolve_collision(destination, "sessionid123")

    assert resolved == destination.with_name("rollout-a.sessioni.dup2.jsonl")


def test_resolve_collision_uses_unknown_suffix_when_session_id_missing(tmp_path: Path) -> None:
    destination = tmp_path / "trash" / "sessions" / "2026" / "03" / "30" / "rollout-a.jsonl"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("existing", encoding="utf-8")

    resolved = resolve_collision(destination, "")

    assert resolved == destination.with_name("rollout-a.unknown.dup1.jsonl")


def test_move_record_to_trash_uses_unknown_suffix_for_normalized_missing_session_id(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    source = session_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("session-data", encoding="utf-8")
    planned_destination = trash_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    planned_destination.parent.mkdir(parents=True, exist_ok=True)
    planned_destination.write_text("existing", encoding="utf-8")
    record = make_record(source, "unknown")

    result = move_record_to_trash(record, session_root, trash_root)

    assert result.ok is True
    assert result.planned_destination == planned_destination
    assert result.destination == planned_destination.with_name("rollout-a.unknown.dup1.jsonl")
    assert result.destination.exists()


def test_trash_records_returns_failed_item_for_path_outside_session_root_and_continues(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    bad_source = tmp_path / "outside" / "rollout-bad.jsonl"
    good_source = session_root / "2026" / "03" / "30" / "nested" / "rollout-good.jsonl"
    bad_source.parent.mkdir(parents=True, exist_ok=True)
    good_source.parent.mkdir(parents=True, exist_ok=True)
    bad_source.write_text("bad", encoding="utf-8")
    good_source.write_text("good", encoding="utf-8")

    bad_record = make_record(bad_source, "bad-session")
    good_record = make_record(good_source, "good-session")

    result = trash_records([bad_record, good_record], session_root, trash_root)

    assert result.total == 2
    assert result.failed == 1
    assert result.succeeded == 1
    assert result.items[0].ok is False
    assert result.items[0].planned_destination is None
    assert result.items[0].destination is None
    assert result.items[0].move_error is not None
    assert result.items[0].prune_error is None
    assert result.items[1].ok is True
    assert result.items[1].destination.exists()


def test_move_record_to_trash_reports_prune_failure_without_marking_move_failed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    source = session_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("session-data", encoding="utf-8")
    record = make_record(source, "alpha")

    def fake_prune(start_dir: Path, session_root: Path) -> list[Path]:
        raise OSError("prune failed")

    monkeypatch.setattr(trash_module, "prune_empty_session_dirs", fake_prune)

    result = move_record_to_trash(record, session_root, trash_root)

    assert result.destination.exists()
    assert result.move_ok is True
    assert result.prune_ok is False
    assert result.move_error is None
    assert result.prune_error is not None


def test_move_record_to_trash_preserves_planned_destination_when_move_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    source = session_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("session-data", encoding="utf-8")
    record = make_record(source, "alpha")

    def fake_move(src: str, dst: str, copy_function: object = trash_module.shutil.copy2) -> str:
        raise OSError("move failed")

    monkeypatch.setattr(trash_module.shutil, "move", fake_move)

    result = move_record_to_trash(record, session_root, trash_root)

    assert result.move_ok is False
    assert result.prune_ok is False
    assert result.planned_destination == trash_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    assert result.destination == trash_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    assert result.move_error is not None
    assert result.prune_error is None


def test_move_record_to_trash_leaves_paths_unset_when_planning_fails(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    source = tmp_path / "outside" / "rollout-a.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("session-data", encoding="utf-8")
    record = make_record(source, "alpha")

    result = move_record_to_trash(record, session_root, trash_root)

    assert result.move_ok is False
    assert result.prune_ok is False
    assert result.planned_destination is None
    assert result.destination is None
    assert result.move_error is not None
    assert result.prune_error is None


def test_move_record_to_trash_creates_destination_parent_directories(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    source = session_root / "2026" / "03" / "30" / "nested" / "rollout-a.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("session-data", encoding="utf-8")
    record = make_record(source, "alpha")

    result = move_record_to_trash(record, session_root, trash_root)

    assert result.ok is True
    assert result.destination.exists()
    assert result.destination.parent.exists()


def test_prune_empty_session_dirs_removes_only_empty_directories_within_session_root(tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    source_dir = session_root / "2026" / "03" / "30" / "empty" / "nested"
    keep_dir = session_root / "2026" / "03" / "30" / "keep"
    source_dir.mkdir(parents=True, exist_ok=True)
    keep_dir.mkdir(parents=True, exist_ok=True)
    (keep_dir / "other.jsonl").write_text("keep", encoding="utf-8")

    prune_empty_session_dirs(source_dir, session_root)

    assert not source_dir.exists()
    assert not source_dir.parent.exists()
    assert keep_dir.exists()
    assert (session_root / "2026" / "03" / "30").exists()
    assert (session_root / "2026" / "03").exists()


def test_trash_records_continues_after_one_move_fails_and_summarizes_results(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    session_root = tmp_path / "sessions"
    trash_root = tmp_path / "trash" / "sessions"
    first_path = session_root / "2026" / "03" / "30" / "first" / "rollout-a.jsonl"
    second_path = session_root / "2026" / "03" / "30" / "second" / "rollout-b.jsonl"
    first_path.parent.mkdir(parents=True, exist_ok=True)
    second_path.parent.mkdir(parents=True, exist_ok=True)
    first_path.write_text("first", encoding="utf-8")
    second_path.write_text("second", encoding="utf-8")

    first = make_record(first_path, "first-session")
    second = make_record(second_path, "second-session")

    original_move = trash_module.shutil.move

    def fake_move(src: str, dst: str, copy_function: object = trash_module.shutil.copy2) -> str:
        source_path = Path(src)
        if source_path == first_path:
            raise OSError("cannot move first record")
        return original_move(src, dst, copy_function=copy_function)

    monkeypatch.setattr(trash_module.shutil, "move", fake_move)

    result = trash_records([first, second], session_root, trash_root)

    assert result.total == 2
    assert result.succeeded == 1
    assert result.failed == 1
    assert len(result.items) == 2
    assert result.items[0].ok is False
    assert result.items[1].ok is True
    assert result.items[1].destination.exists()
