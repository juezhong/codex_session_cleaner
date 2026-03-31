# Copyright 2026 liyunfeng
# Licensed under the Apache License, Version 2.0

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class SessionRecord:
    session_id: str
    cwd: str | None
    jsonl_path: Path
    created_at: datetime | None
    updated_at: datetime | None
    display_label: str
    session_kind: str = "main"
    session_label: str | None = None
    conversation_preview: tuple[str, ...] = field(default_factory=tuple)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionParseResult:
    record: SessionRecord
