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

from datetime import datetime

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import ListView, Static

from codex_session_cleaner.models import ConversationRound, SessionRecord

_ELLIPSIS_SEGMENT = object()


class MessageCard(Static):
    def __init__(self, content: str, role: str) -> None:
        classes = "message-card"
        classes = f"{classes} message-card-{role}"
        super().__init__(content, markup=False, classes=classes)


class DetailsPane(VerticalScroll):
    can_focus = False
    can_focus_children = False

    DEFAULT_CSS = """
    DetailsPane {
        background: $panel;
    }

    #details-body {
        width: 100%;
        height: auto;
        padding: 1 1 1 1;
    }

    .metadata-row {
        color: $text-muted;
        padding: 0 1 0 1;
    }

    .message-card {
        border: round #4a5568;
        padding: 1 1 1 1;
        margin: 0 0 1 0;
    }

    .message-card-user {
        background: #2f3747;
        color: $text;
    }

    .message-card-assistant {
        background: #243849;
        color: $text;
    }

    .ellipsis-row {
        content-align: center middle;
        color: $text-muted;
        padding: 0 0 0 0;
        margin: 0 0 1 0;
    }

    .warning-row {
        border: round #b86a6a;
        background: #4a2424;
        color: $text;
        padding: 1 1 1 1;
        margin: 0 0 1 0;
    }
    """

    def compose(self) -> ComposeResult:
        yield Vertical(id="details-body")

    def on_mouse_down(self, _: events.MouseDown) -> None:
        self.call_after_refresh(self._restore_session_list_focus)

    def show_record(self, record: SessionRecord | None, empty_text: str) -> None:
        body = self.query_one("#details-body", Vertical)
        body.remove_children()
        if record is None:
            body.mount(Static(empty_text, markup=False))
            self.scroll_home(animate=False)
            return

        widgets: list[Static] = []
        widgets.extend(_build_metadata_widgets(record))

        convo_widgets = _build_conversation_widgets(record.conversation_rounds)
        if convo_widgets:
            widgets.append(Static("conversation:", classes="metadata-row", markup=False))
            widgets.extend(convo_widgets)

        warning_widgets = _build_warning_widgets(record.warnings)
        if warning_widgets:
            widgets.append(Static("warnings:", classes="metadata-row", markup=False))
            widgets.extend(warning_widgets)

        if not widgets:
            body.mount(Static(empty_text, markup=False))
        else:
            body.mount(*widgets)

        self.scroll_home(animate=False)

    def _restore_session_list_focus(self) -> None:
        try:
            self.screen.query_one("#session-list", ListView).focus()
        except NoMatches:
            return


def _build_metadata_widgets(record: SessionRecord) -> list[Static]:
    metadata = [
        ("cwd", record.cwd or "unknown"),
        ("type", _format_identity_label(record) or "main"),
        ("path", record.jsonl_path),
        ("updated", _format_datetime(record.updated_at)),
    ]
    return [Static(f"{label}: {value}", classes="metadata-row", markup=False) for label, value in metadata]


def _build_conversation_widgets(rounds: tuple[ConversationRound, ...]) -> list[Static]:
    widgets: list[Static] = []
    segments = _visible_conversation_segments(rounds)
    for segment in segments:
        if segment is _ELLIPSIS_SEGMENT:
            widgets.append(Static("...", classes="ellipsis-row", markup=False))
            continue

        user_text = _truncate_user_text(_normalize_for_display(segment.user_text))
        widgets.append(MessageCard(user_text, role="user"))

        assistant_text = segment.assistant_text
        if assistant_text:
            assistant_line = _truncate_assistant_text(_normalize_for_display(assistant_text))
            widgets.append(MessageCard(assistant_line, role="assistant"))
    return widgets


def _build_warning_widgets(warnings: list[str]) -> list[Static]:
    return [Static(warning, classes="warning-row", markup=False) for warning in warnings]


def _visible_conversation_segments(rounds: tuple[ConversationRound, ...]) -> list[ConversationRound | object]:
    total = len(rounds)
    if total <= 7:
        return list(rounds)
    return [*rounds[:3], _ELLIPSIS_SEGMENT, *rounds[-4:]]


def _truncate_user_text(text: str) -> str:
    return _truncate_text(text, 200)


def _truncate_assistant_text(text: str) -> str:
    return _truncate_text(text, 300)


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    trimmed = text[: limit - 3].rstrip()
    if not trimmed:
        trimmed = text[: limit - 3]
    return f"{trimmed}..."


def _normalize_for_display(text: str) -> str:
    return " ".join(text.split())


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone().isoformat(timespec="seconds")


def _format_identity_label(record: SessionRecord) -> str:
    if record.session_kind == "subagent":
        if record.session_label:
            return f"subagent {record.session_label}"
        return "subagent"
    return "main"
