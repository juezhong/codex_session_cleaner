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
from datetime import datetime
from pathlib import Path


@dataclass(slots=True)
class ConversationRound:
    user_text: str
    assistant_text: str | None = None


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
    conversation_rounds: tuple[ConversationRound, ...] = field(default_factory=tuple)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SessionParseResult:
    record: SessionRecord
