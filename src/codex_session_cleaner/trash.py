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

from dataclasses import dataclass, field
import os
import shutil
from pathlib import Path
from typing import Iterable

from codex_session_cleaner.models import SessionRecord


@dataclass(slots=True)
class TrashMoveResult:
    record: SessionRecord
    source: Path
    planned_destination: Path | None = None
    destination: Path | None = None
    move_ok: bool = False
    prune_ok: bool = False
    move_error: Exception | None = None
    prune_error: Exception | None = None
    pruned_dirs: tuple[Path, ...] = ()

    @property
    def ok(self) -> bool:
        return self.move_ok and self.prune_ok


@dataclass(slots=True)
class TrashBatchResult:
    items: list[TrashMoveResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def succeeded(self) -> int:
        return sum(1 for item in self.items if item.ok)

    @property
    def failed(self) -> int:
        return self.total - self.succeeded


def get_trash_root() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "trash" / "sessions"
    home = Path(os.environ.get("HOME", str(Path.home())))
    return home / ".codex" / "trash" / "sessions"


def build_trash_path(record: SessionRecord, session_root: Path, trash_root: Path) -> Path:
    relative_path = record.jsonl_path.relative_to(session_root)
    return trash_root / relative_path


def resolve_collision(destination: Path, session_id: str) -> Path:
    if not destination.exists():
        return destination

    short_session_id = _short_session_id(session_id)
    if short_session_id:
        first_choice = _with_collision_suffix(destination, short_session_id)
        if not first_choice.exists():
            return first_choice

        index = 1
        while True:
            candidate = _with_duplicate_suffix(destination, short_session_id, index)
            if not candidate.exists():
                return candidate
            index += 1

    index = 1
    while True:
        candidate = _with_unknown_suffix(destination, index)
        if not candidate.exists():
            return candidate
        index += 1


def move_record_to_trash(record: SessionRecord, session_root: Path, trash_root: Path) -> TrashMoveResult:
    source = record.jsonl_path
    result = TrashMoveResult(
        record=record,
        source=source,
    )

    try:
        planned_destination = build_trash_path(record, session_root, trash_root)
        destination = resolve_collision(planned_destination, record.session_id)
        result.planned_destination = planned_destination
        result.destination = destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
    except Exception as exc:  # pragma: no cover - exercised through tests
        result.move_error = exc
        return result

    result.move_ok = True
    try:
        pruned_dirs = prune_empty_session_dirs(source.parent, session_root)
    except Exception as exc:  # pragma: no cover - exercised through tests
        result.prune_error = exc
        return result

    result.prune_ok = True
    result.pruned_dirs = tuple(pruned_dirs)
    return result


def prune_empty_session_dirs(start_dir: Path, session_root: Path) -> list[Path]:
    removed: list[Path] = []
    current = start_dir

    if current == session_root or session_root not in current.parents:
        return removed

    while current != session_root and session_root in current.parents:
        if current.exists() and current.is_dir():
            try:
                next(current.iterdir())
            except StopIteration:
                current.rmdir()
                removed.append(current)
                current = current.parent
                continue
        break

    return removed


def trash_records(records: Iterable[SessionRecord], session_root: Path, trash_root: Path) -> TrashBatchResult:
    items = [move_record_to_trash(record, session_root, trash_root) for record in records]
    return TrashBatchResult(items=items)


def _short_session_id(session_id: str) -> str:
    stripped = session_id.strip()
    if not stripped or stripped == "unknown":
        return ""
    return stripped[:8]


def _with_collision_suffix(destination: Path, short_session_id: str) -> Path:
    return destination.with_name(f"{destination.stem}.{short_session_id}{destination.suffix}")


def _with_duplicate_suffix(destination: Path, short_session_id: str, index: int) -> Path:
    return destination.with_name(f"{destination.stem}.{short_session_id}.dup{index}{destination.suffix}")


def _with_unknown_suffix(destination: Path, index: int) -> Path:
    return destination.with_name(f"{destination.stem}.unknown.dup{index}{destination.suffix}")
