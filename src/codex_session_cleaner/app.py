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

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from codex_session_cleaner.discovery import discover_sessions, get_session_root
from codex_session_cleaner.models import SessionRecord
from codex_session_cleaner.trash import TrashMoveResult, get_trash_root, trash_records


@dataclass(frozen=True, slots=True)
class ConfirmationSelection:
    jsonl_path: Path
    cwd: str | None
    session_id: str

    @property
    def short_session_id(self) -> str:
        return _short_session_id(self.session_id)


@dataclass(frozen=True, slots=True)
class ConfirmationData:
    selected_count: int
    selected_records: tuple[ConfirmationSelection, ...]


def _format_row(record: SessionRecord, selected: bool) -> str:
    selection_marker = "[x]" if selected else "[ ]"
    cwd_label = record.cwd or "unknown"
    updated_label = _format_datetime(record.updated_at)
    identity_label = _format_identity_label(record)
    cleanup_error = _cleanup_error_message(record)
    header = f"{selection_marker} {cwd_label}"
    if identity_label is not None:
        header = f"{header}  |  {identity_label}"
    header = f"{header}  |  {updated_label}"
    if cleanup_error is not None:
        header = f"{header}  {cleanup_error}"
    return header


def _render_details(record: SessionRecord) -> str:
    lines = [
        f"cwd: {record.cwd or 'unknown'}",
        f"type: {_format_identity_label(record) or 'main'}",
        f"path: {record.jsonl_path}",
        f"updated: {_format_datetime(record.updated_at)}",
    ]
    preview_lines = _format_detailed_preview(record.conversation_preview, limit=15)
    if preview_lines:
        lines.append("")
        lines.append("conversation:")
        lines.extend(f"- {preview}" for preview in preview_lines)
    if record.warnings:
        lines.append("")
        lines.append("warnings:")
        lines.extend(f"- {warning}" for warning in record.warnings)
    return "\n".join(lines)


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "unknown"
    return value.astimezone().isoformat(timespec="seconds")


def _short_session_id(session_id: str) -> str:
    if session_id == "unknown":
        return session_id
    return session_id[:8]


def _format_detailed_preview(preview: tuple[str, ...], *, limit: int) -> list[str]:
    snippets = _ordered_preview_snippets(preview, limit=limit)
    return [snippet for snippet in snippets]


def _ordered_preview_snippets(preview: tuple[str, ...], *, limit: int) -> tuple[str, ...]:
    if not preview:
        return tuple()
    return preview[:limit]


def _format_identity_label(record: SessionRecord) -> str | None:
    if record.session_kind == "subagent":
        if record.session_label:
            return f"subagent {record.session_label}"
        return "subagent"
    return "main"


def _cleanup_error_message(record: SessionRecord) -> str | None:
    for warning in record.warnings:
        if warning.startswith("cleanup error: "):
            return warning
    return None


class SessionListItem(ListItem):
    def __init__(self, record: SessionRecord, generation: int, selected: bool) -> None:
        super().__init__(Label(_row_text(record, selected), markup=False))
        self.record = record
        self.generation = generation
        self.selected = selected

    def update_selected(self, selected: bool) -> None:
        self.selected = selected
        self.query_one(Label).update(_row_text(self.record, selected))


def _row_text(record: SessionRecord, selected: bool) -> Text:
    return Text(_format_row(record, selected), no_wrap=True, overflow="crop")


def _render_confirmation_text(data: ConfirmationData) -> str:
    lines = [f"Selected sessions: {data.selected_count}", ""]
    for record in data.selected_records:
        lines.append(f"cwd: {record.cwd or 'unknown'}")
        lines.append(f"session id: {record.session_id}")
        lines.append(f"path: {record.jsonl_path}")
        lines.append(f"short id: {record.short_session_id}")
        lines.append("")
    if lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


class ConfirmationScreen(ModalScreen[bool]):
    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("n", "cancel", "Cancel"),
        ("y", "confirm", "Confirm"),
        ("enter", "confirm", "Confirm"),
        ("j", "scroll_down", "Down"),
        ("k", "scroll_up", "Up"),
    ]

    def __init__(self, confirmation: ConfirmationData) -> None:
        super().__init__()
        self.confirmation = confirmation
        self.selected_count = confirmation.selected_count

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(Text("[y] Confirm  [n] Cancel  [j/k] Scroll"), id="confirmation-shortcuts"),
            VerticalScroll(
                Static(_render_confirmation_text(self.confirmation), id="confirmation-body"),
                id="confirmation-scroll",
            ),
            id="confirmation-dialog",
        )

    def on_mount(self) -> None:
        scroll = self.query_one("#confirmation-scroll", VerticalScroll)
        scroll.focus()

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_scroll_down(self) -> None:
        self.query_one("#confirmation-scroll", VerticalScroll).scroll_down(animate=False)

    def action_scroll_up(self) -> None:
        self.query_one("#confirmation-scroll", VerticalScroll).scroll_up(animate=False)


class CodexSessionCleanerApp(App[None]):
    TITLE = "Codex Session Cleaner"

    CSS = """
    Screen {
        layout: vertical;
    }

    #toolbar {
        height: auto;
        padding: 0 1;
    }

    #status-line {
        height: auto;
        padding: 0 1;
    }

    #main-pane {
        height: 1fr;
        padding: 0 1;
    }

    #session-list {
        width: 42%;
        min-width: 30;
    }

    #details {
        width: 58%;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.session_root: Path = get_session_root()
        self.view_mode = "all"
        self.records: list[SessionRecord] = []
        self.visible_records: list[SessionRecord] = []
        self.selected_record_paths: set[Path] = set()
        self.highlighted_record_path: Path | None = None
        self.session_list_generation = 0
        self.cleanup_summary = ""
        self.cleanup_failed_records_by_path: dict[Path, SessionRecord] = {}

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("", id="status-line"),
            Horizontal(
                ListView(id="session-list"),
                Static(self.get_empty_state_renderable(), id="details"),
                id="main-pane",
            ),
            Footer(),
            id="toolbar",
        )

    def on_mount(self) -> None:
        self._load_sessions()

    def on_key(self, event) -> None:
        key = getattr(event, "key", None)
        if key is None:
            return
        if isinstance(self.screen, ConfirmationScreen):
            return
        if key == "q":
            event.stop()
            self.exit()
            return
        if key == "r":
            event.stop()
            self._load_sessions()
            return
        if key == "tab":
            event.stop()
            self.cycle_view_mode()
            return
        if key == "space":
            event.stop()
            self.toggle_selection_for_highlighted_row()
            return
        if key == "a":
            event.stop()
            self.select_all_visible()
            return
        if key == "u":
            event.stop()
            self.clear_all_visible()
            return
        if key == "j":
            event.stop()
            self.move_highlight(1)
            return
        if key == "k":
            event.stop()
            self.move_highlight(-1)
            return
        if key == "d" and self.selected_record_paths:
            event.stop()
            self.open_confirmation()
            return

    def _load_sessions(self) -> None:
        self.records = discover_sessions(self.session_root)
        self._merge_cleanup_failed_records()
        self._prune_selected_record_paths()
        self.apply_view_mode(refresh=False)
        if self.highlighted_record_path is None and self.visible_records:
            self.highlighted_record_path = self.visible_records[0].jsonl_path
        self._refresh_widgets()

    def _prune_selected_record_paths(self) -> None:
        valid_paths = {record.jsonl_path for record in self.records}
        self.selected_record_paths.intersection_update(valid_paths)

    def cycle_view_mode(self) -> None:
        modes = ("all", "main", "subagent")
        current_index = modes.index(self.view_mode)
        self.view_mode = modes[(current_index + 1) % len(modes)]
        self.apply_view_mode()

    def apply_view_mode(self, *, refresh: bool = True) -> None:
        if self.view_mode == "all":
            self.visible_records = list(self.records)
        else:
            self.visible_records = [record for record in self.records if record.session_kind == self.view_mode]

        if self.highlighted_record_path not in {record.jsonl_path for record in self.visible_records}:
            self.highlighted_record_path = self.visible_records[0].jsonl_path if self.visible_records else None
        if refresh:
            self._refresh_widgets()

    def toggle_selection_for_highlighted_row(self) -> None:
        session_list = self._query_widget("#session-list", ListView)
        record = self._record_for_path(self.highlighted_record_path)
        if record is None and self.visible_records:
            record = self.visible_records[0]
        if record is None:
            return
        if record.jsonl_path in self.selected_record_paths:
            self.selected_record_paths.remove(record.jsonl_path)
        else:
            self.selected_record_paths.add(record.jsonl_path)

        highlighted_item = self._highlighted_list_item(session_list)
        if highlighted_item is not None and highlighted_item.record.jsonl_path == record.jsonl_path:
            highlighted_item.update_selected(record.jsonl_path in self.selected_record_paths)

        status_line = self._query_widget("#status-line", Static)
        if status_line is not None:
            status_line.update(self._render_status_line())

    def move_highlight(self, delta: int) -> None:
        session_list = self._query_widget("#session-list", ListView)
        if session_list is None or not self.visible_records:
            return

        current_index = session_list.index
        if not isinstance(current_index, int) or current_index < 0:
            current_index = 0

        next_index = max(0, min(len(self.visible_records) - 1, current_index + delta))
        if next_index == current_index:
            return

        session_list.index = next_index

    def select_all_visible(self) -> None:
        visible_paths = {record.jsonl_path for record in self.visible_records}
        if not visible_paths:
            return

        self.selected_record_paths.update(visible_paths)
        self._refresh_visible_selection_widgets()

    def clear_all_visible(self) -> None:
        visible_paths = {record.jsonl_path for record in self.visible_records}
        if not visible_paths:
            return

        self.selected_record_paths.difference_update(visible_paths)
        self._refresh_visible_selection_widgets()

    def _refresh_visible_selection_widgets(self) -> None:
        session_list = self._query_widget("#session-list", ListView)
        if session_list is not None:
            for child in session_list.children:
                if isinstance(child, SessionListItem):
                    child.update_selected(child.record.jsonl_path in self.selected_record_paths)
        status_line = self._query_widget("#status-line", Static)
        if status_line is not None:
            status_line.update(self._render_status_line())

    def build_confirmation_data(self) -> ConfirmationData | None:
        selected_records = tuple(
            ConfirmationSelection(
                jsonl_path=record.jsonl_path,
                cwd=record.cwd,
                session_id=record.session_id,
            )
            for record in self.records
            if record.jsonl_path in self.selected_record_paths
        )
        if not selected_records:
            return None
        return ConfirmationData(
            selected_count=len(selected_records),
            selected_records=selected_records,
        )

    def open_confirmation(self) -> None:
        confirmation = self.build_confirmation_data()
        if confirmation is None:
            return

        def handle_confirmation_result(confirmed: bool) -> None:
            if confirmed:
                self._execute_cleanup(confirmation)

        self.push_screen(ConfirmationScreen(confirmation), callback=handle_confirmation_result)

    def _execute_cleanup(self, confirmation: ConfirmationData) -> None:
        records_to_clean = tuple(self._record_from_confirmation(selection) for selection in confirmation.selected_records)
        result = trash_records(records_to_clean, self.session_root, get_trash_root())
        self.cleanup_summary = self._render_cleanup_summary(result)
        self._apply_cleanup_result(result)
        self._refresh_widgets()

    def _apply_cleanup_result(self, result) -> None:
        successful_paths = {item.record.jsonl_path for item in result.items if item.ok}
        failed_records_by_path = {
            item.record.jsonl_path: self._cleanup_failure_record(item) for item in result.items if not item.ok
        }

        self.records = [record for record in self.records if record.jsonl_path not in successful_paths]
        self.selected_record_paths.difference_update(successful_paths)

        for jsonl_path in successful_paths:
            self.cleanup_failed_records_by_path.pop(jsonl_path, None)

        records_by_path = {record.jsonl_path: record for record in self.records}
        for jsonl_path, failed_record in failed_records_by_path.items():
            existing = records_by_path.get(jsonl_path)
            if existing is None:
                self.records.append(failed_record)
                records_by_path[jsonl_path] = failed_record
                continue
            self._merge_cleanup_record_warnings(existing, failed_record)

        self.cleanup_failed_records_by_path.update(failed_records_by_path)
        self.apply_view_mode(refresh=False)

    def _record_from_confirmation(self, selection: ConfirmationSelection) -> SessionRecord:
        return SessionRecord(
            session_id=selection.session_id,
            cwd=selection.cwd,
            jsonl_path=selection.jsonl_path,
            created_at=None,
            updated_at=None,
            display_label=f"{selection.cwd or 'unknown'} · {selection.short_session_id}",
        )

    def _cleanup_error_message(self, result: TrashMoveResult) -> str:
        if result.move_error is not None:
            return f"cleanup error: {result.move_error}"
        if result.prune_error is not None:
            return f"cleanup error: {result.prune_error}"
        return "cleanup error: unknown failure"

    def _cleanup_failure_record(self, result: TrashMoveResult) -> SessionRecord:
        cleanup_error = self._cleanup_error_message(result)
        warnings = [warning for warning in result.record.warnings if not warning.startswith("cleanup error: ")]
        warnings.append(cleanup_error)
        return SessionRecord(
            session_id=result.record.session_id,
            cwd=result.record.cwd,
            jsonl_path=result.record.jsonl_path,
            created_at=result.record.created_at,
            updated_at=result.record.updated_at,
            display_label=result.record.display_label,
            warnings=warnings,
        )

    def _merge_cleanup_failed_records(self) -> None:
        if not self.cleanup_failed_records_by_path:
            return

        records_by_path = {record.jsonl_path: record for record in self.records}
        for jsonl_path, failed_record in self.cleanup_failed_records_by_path.items():
            existing = records_by_path.get(jsonl_path)
            if existing is None:
                self.records.append(failed_record)
                continue
            self._merge_cleanup_record_warnings(existing, failed_record)

    def _merge_cleanup_record_warnings(self, target: SessionRecord, source: SessionRecord) -> None:
        target_warnings = [warning for warning in target.warnings if not warning.startswith("cleanup error: ")]
        for warning in source.warnings:
            if warning.startswith("cleanup error: ") and warning not in target_warnings:
                target_warnings.append(warning)
        target.warnings = target_warnings

    def _render_cleanup_summary(self, result) -> str:
        if result.total == 0:
            return "Cleanup: no selected sessions were moved"
        summary = f"Cleanup: {result.succeeded} moved, {result.failed} failed"
        if result.failed == 0:
            return summary + " | all selected sessions moved to trash"
        return summary

    def _refresh_widgets(self) -> None:
        session_list = self._query_widget("#session-list", ListView)
        details = self._query_widget("#details", Static)
        status_line = self._query_widget("#status-line", Static)

        highlighted_path = self.highlighted_record_path
        self._render_session_list(session_list, highlighted_path)
        effective_highlighted_path = self._effective_highlighted_path(highlighted_path)
        self.highlighted_record_path = effective_highlighted_path

        if details is not None:
            highlighted_record = self._record_for_path(effective_highlighted_path)
            details.update(_render_details(highlighted_record) if highlighted_record else self.get_empty_state_renderable())

        if status_line is not None:
            status_line.update(self._render_status_line())

    def _query_widget(self, selector: str, widget_type: type[object]) -> object | None:
        try:
            return self.query_one(selector, expect_type=widget_type)
        except NoMatches:
            return None

    def _render_session_list(
        self, session_list: ListView | None, highlighted_path: Path | None
    ) -> None:
        if session_list is None:
            return

        had_focus = session_list.has_focus
        self.session_list_generation += 1
        generation = self.session_list_generation

        session_list.clear()
        for record in self.visible_records:
            session_list.append(SessionListItem(record, generation, record.jsonl_path in self.selected_record_paths))

        if not self.visible_records:
            return

        if highlighted_path is not None:
            highlighted_index = self._index_for_path(highlighted_path)
            session_list.index = highlighted_index if highlighted_index is not None else 0
        else:
            current_index = session_list.index
            if not isinstance(current_index, int) or current_index < 0:
                session_list.index = 0

        if had_focus or self.focused is None:
            session_list.focus()

    def _highlighted_path(self, session_list: ListView | None) -> Path | None:
        record = self._highlighted_record(session_list)
        return record.jsonl_path if record is not None else None

    def _effective_highlighted_path(self, highlighted_path: Path | None) -> Path | None:
        if highlighted_path is not None and self._index_for_path(highlighted_path) is not None:
            return highlighted_path
        if self.visible_records:
            return self.visible_records[0].jsonl_path
        return None

    def _highlighted_record(self, session_list: ListView | None) -> SessionRecord | None:
        highlighted_item = self._highlighted_list_item(session_list)
        if highlighted_item is None:
            return None
        return highlighted_item.record

    def _highlighted_list_item(self, session_list: ListView | None) -> SessionListItem | None:
        if session_list is None:
            return None
        highlighted_item = session_list.highlighted_child
        if highlighted_item is None:
            return None
        if not isinstance(highlighted_item, SessionListItem):
            return None
        return highlighted_item

    def _index_for_path(self, jsonl_path: Path) -> int | None:
        for index, record in enumerate(self.visible_records):
            if record.jsonl_path == jsonl_path:
                return index
        return None

    def _record_for_path(self, jsonl_path: Path | None) -> SessionRecord | None:
        if jsonl_path is None:
            return None
        for record in self.visible_records:
            if record.jsonl_path == jsonl_path:
                return record
        return None

    def _render_status_line(self) -> str:
        total = len(self.records)
        visible = len(self.visible_records)
        selected = len(self.selected_record_paths)
        base = (
            f"view: {self.view_mode} | {visible} visible of {total} total | "
            f"{selected} selected | tab switch view | a select all | u clear all | r refresh | space toggle | d confirm | q quit"
        )
        if self.cleanup_summary:
            return f"{base}\n{self.cleanup_summary}"
        return base

    def get_empty_state_renderable(self) -> str:
        return (
            "No Codex sessions found.\n\n"
            "The app will scan the local Codex session store and show any\n"
            "rollout-*.jsonl files it finds."
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            if self.visible_records:
                return
            details = self._query_widget("#details", Static)
            if details is not None:
                details.update(self.get_empty_state_renderable())
            return

        record = getattr(event.item, "record", None)
        if record is None:
            return
        if getattr(event.item, "generation", None) != self.session_list_generation:
            return

        self.highlighted_record_path = record.jsonl_path
        details = self._query_widget("#details", Static)
        if details is not None:
            details.update(_render_details(record))
