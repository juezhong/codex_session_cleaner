from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from textual.widgets import Label, ListView, Static

from codex_session_cleaner.app import CodexSessionCleanerApp
from codex_session_cleaner.app import ConfirmationScreen
from codex_session_cleaner.app import _format_row
from codex_session_cleaner.app import _render_details
from codex_session_cleaner.models import SessionRecord
from codex_session_cleaner.trash import TrashBatchResult, TrashMoveResult


def make_record(
    path: Path,
    session_id: str,
    cwd: str | None,
    updated_at: datetime,
    *,
    conversation_preview: tuple[str, ...] = (),
) -> SessionRecord:
    return SessionRecord(
        session_id=session_id,
        cwd=cwd,
        jsonl_path=path,
        created_at=updated_at,
        updated_at=updated_at,
        display_label=f"{cwd or 'unknown'} · {session_id[:8] if session_id != 'unknown' else 'unknown'}",
        conversation_preview=conversation_preview,
    )


def test_codex_session_cleaner_app_constructs() -> None:
    app = CodexSessionCleanerApp()

    assert app is not None


def test_format_row_and_details_use_cwd_first_preview_without_session_id(tmp_path: Path) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-a.jsonl",
        "session-alpha",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
        conversation_preview=(
            "user: first question",
            "assistant: first answer",
            "user: second question",
        ),
    )

    row = _format_row(record, selected=False)
    details = _render_details(record)

    assert "/projects/alpha" in row
    assert "main" in row
    assert "first question" not in row
    assert "session-alpha" not in row
    assert "cwd: /projects/alpha" in details
    assert "conversation:" in details
    assert "first question" in details
    assert "session-alpha" not in details


def test_empty_state_renders_on_mounted_app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            details = app.query_one("#details", Static)
            session_list = app.query_one("#session-list", ListView)

            assert "No Codex sessions found" in str(details.renderable)
            assert len(session_list.children) == 0

    asyncio.run(scenario())


def test_tab_cycles_between_all_main_and_subagent_views(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main_record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-main.jsonl",
        "session-main",
        "/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    subagent_record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-subagent.jsonl",
        "session-subagent",
        "/alpha",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    subagent_record.session_kind = "subagent"
    subagent_record.session_label = "helper"
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [main_record, subagent_record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            assert app.view_mode == "all"
            assert [record.jsonl_path for record in app.visible_records] == [
                main_record.jsonl_path,
                subagent_record.jsonl_path,
            ]

            await pilot.press("tab")
            await pilot.pause()

            assert app.view_mode == "main"
            assert [record.jsonl_path for record in app.visible_records] == [main_record.jsonl_path]

            await pilot.press("tab")
            await pilot.pause()

            assert app.view_mode == "subagent"
            assert [record.jsonl_path for record in app.visible_records] == [subagent_record.jsonl_path]

            await pilot.press("tab")
            await pilot.pause()

            assert app.view_mode == "all"

    asyncio.run(scenario())


def test_refresh_preserves_highlight_by_record_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-a.jsonl",
        "session-alpha",
        "/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    b = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-b.jsonl",
        "session-bravo",
        "/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    c = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-c.jsonl",
        "session-charlie",
        "/charlie",
        datetime(2026, 3, 30, 3, 0, tzinfo=timezone.utc),
    )
    discovered: list[list[SessionRecord]] = [[a, b, c], [c, a]]

    def fake_discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
        return list(discovered.pop(0))

    monkeypatch.setattr("codex_session_cleaner.app.discover_sessions", fake_discover_sessions)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            details = app.query_one("#details", Static)

            session_list.index = 2
            await pilot.pause()

            assert c.jsonl_path.name in str(details.renderable)
            assert app.focused is session_list

            app._load_sessions()
            await pilot.pause()

            assert session_list.index == 0
            assert session_list.highlighted_child is not None
            assert getattr(session_list.highlighted_child, "record").jsonl_path == c.jsonl_path
            assert c.jsonl_path.name in str(details.renderable)
            assert app.focused is session_list

    asyncio.run(scenario())


def test_view_switch_preserves_selection_state_and_highlighted_path(
    tmp_path: Path,
) -> None:
    app = CodexSessionCleanerApp()
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    bravo.session_kind = "subagent"
    app.records = [alpha, bravo]
    app.visible_records = list(app.records)
    app.selected_record_paths.add(alpha.jsonl_path)
    app.highlighted_record_path = bravo.jsonl_path
    app.view_mode = "subagent"

    app.apply_view_mode()

    assert app.selected_record_paths == {alpha.jsonl_path}
    assert app.highlighted_record_path == bravo.jsonl_path
    assert [record.jsonl_path for record in app.visible_records] == [bravo.jsonl_path]


def test_build_confirmation_data_uses_selected_records(
    tmp_path: Path,
) -> None:
    app = CodexSessionCleanerApp()
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    app.records = [alpha, bravo]
    app.visible_records = list(app.records)
    app.selected_record_paths = {alpha.jsonl_path, bravo.jsonl_path}

    confirmation = app.build_confirmation_data()

    assert confirmation is not None
    assert confirmation.selected_count == 2
    assert [record.jsonl_path for record in confirmation.selected_records] == [
        alpha.jsonl_path,
        bravo.jsonl_path,
    ]
    assert [record.short_session_id for record in confirmation.selected_records] == [
        "alpha123",
        "bravo123",
    ]


def test_confirmation_snapshot_retains_exact_selected_paths_after_app_mutation(
    tmp_path: Path,
) -> None:
    app = CodexSessionCleanerApp()
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    app.records = [alpha, bravo]
    app.visible_records = list(app.records)
    app.selected_record_paths = {alpha.jsonl_path, bravo.jsonl_path}

    confirmation = app.build_confirmation_data()
    assert confirmation is not None

    app.records = [bravo]
    app.selected_record_paths = {bravo.jsonl_path}

    assert [record.jsonl_path for record in confirmation.selected_records] == [
        alpha.jsonl_path,
        bravo.jsonl_path,
    ]
    assert [record.short_session_id for record in confirmation.selected_records] == [
        "alpha123",
        "bravo123",
    ]


def test_reload_prunes_stale_selected_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    discovered: list[list[SessionRecord]] = [[alpha, bravo], [alpha]]

    def fake_discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
        return list(discovered.pop(0))

    monkeypatch.setattr("codex_session_cleaner.app.discover_sessions", fake_discover_sessions)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            app.selected_record_paths = {alpha.jsonl_path, bravo.jsonl_path}
            app._load_sessions()
            await pilot.pause()

            assert app.selected_record_paths == {alpha.jsonl_path}
            assert "1 selected" in str(app.query_one("#status-line", Static).renderable)
            confirmation = app.build_confirmation_data()
            assert confirmation is not None
            assert [record.jsonl_path for record in confirmation.selected_records] == [
                alpha.jsonl_path,
            ]

    asyncio.run(scenario())


def test_confirmation_modal_ignores_space_and_q_while_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()

            await pilot.press("d")
            await pilot.pause()

            assert len(app.screen_stack) == 2
            assert isinstance(app.screen, ConfirmationScreen)
            assert app.selected_record_paths == {record.jsonl_path}
            confirmation_body = app.query_one("#confirmation-body", Static)
            confirmation_text = str(confirmation_body.renderable)
            assert str(record.jsonl_path) in confirmation_text
            assert record.session_id in confirmation_text

            await pilot.press("space")
            await pilot.press("q")
            await pilot.pause()

            assert len(app.screen_stack) == 2
            assert isinstance(app.screen, ConfirmationScreen)
            assert app.selected_record_paths == {record.jsonl_path}
            assert app.focused is not None

    asyncio.run(scenario())


def test_confirmation_modal_supports_y_and_n_shortcuts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            await pilot.press("space")
            await pilot.press("d")
            await pilot.pause()

            assert isinstance(app.screen, ConfirmationScreen)

            await pilot.press("n")
            await pilot.pause()

            assert len(app.screen_stack) == 1

            await pilot.press("d")
            await pilot.pause()

            assert isinstance(app.screen, ConfirmationScreen)

            await pilot.press("y")
            await pilot.pause()

            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_confirmation_modal_supports_jk_scroll_shortcuts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            await pilot.press("space")
            await pilot.press("d")
            await pilot.pause()

            assert isinstance(app.screen, ConfirmationScreen)

            await pilot.press("j")
            await pilot.press("k")
            await pilot.pause()

            assert isinstance(app.screen, ConfirmationScreen)

    asyncio.run(scenario())


def test_main_view_supports_jk_navigation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [alpha, bravo],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            assert session_list.index == 0

            await pilot.press("j")
            await pilot.pause()

            assert session_list.index == 1

            await pilot.press("k")
            await pilot.pause()

            assert session_list.index == 0

    asyncio.run(scenario())


def test_a_selects_all_and_u_clears_all_for_current_view_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    main_record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-main.jsonl",
        "session-main",
        "/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    subagent_record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-subagent.jsonl",
        "session-subagent",
        "/alpha",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    subagent_record.session_kind = "subagent"
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [main_record, subagent_record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            await pilot.press("tab")
            await pilot.pause()

            assert app.view_mode == "main"

            await pilot.press("a")
            await pilot.pause()

            assert app.selected_record_paths == {main_record.jsonl_path}

            await pilot.press("u")
            await pilot.pause()

            assert app.selected_record_paths == set()

            app.selected_record_paths.add(subagent_record.jsonl_path)

            await pilot.press("a")
            await pilot.pause()

            assert app.selected_record_paths == {
                main_record.jsonl_path,
                subagent_record.jsonl_path,
            }

            await pilot.press("u")
            await pilot.pause()

            assert app.selected_record_paths == {subagent_record.jsonl_path}

    asyncio.run(scenario())


def test_status_line_shows_select_all_and_clear_all_shortcuts(
    tmp_path: Path,
) -> None:
    app = CodexSessionCleanerApp()
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    app.records = [alpha, bravo]
    app.visible_records = [alpha]

    assert "a select all" in app._render_status_line()
    assert "u clear all" in app._render_status_line()


def test_space_toggles_highlighted_row_selection_and_d_opens_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "session-alpha",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()

            assert app.selected_record_paths == {record.jsonl_path}
            assert "1 selected" in str(app.query_one("#status-line", Static).renderable)

            await pilot.press("d")
            await pilot.pause()

            assert len(app.screen_stack) == 2
            confirmation_screen = app.screen_stack[-1]
            assert getattr(confirmation_screen, "selected_count", None) == 1

    asyncio.run(scenario())


def test_space_updates_visible_selection_marker_without_rebuilding_list(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "session-alpha",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [record],
    )

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            first_item = session_list.children[0]
            assert "[ ]" in str(first_item.query_one(Label).renderable)

            await pilot.press("space")
            await pilot.pause()

            assert "[x]" in str(first_item.query_one(Label).renderable)
            assert app.selected_record_paths == {record.jsonl_path}

    asyncio.run(scenario())


def test_confirm_uses_confirmation_snapshot_and_triggers_cleanup_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [alpha, bravo],
    )

    cleanup_calls: list[tuple[tuple[SessionRecord, ...], Path, Path]] = []

    def fake_trash_records(records: list[SessionRecord], session_root: Path, trash_root: Path) -> TrashBatchResult:
        cleanup_calls.append((tuple(records), session_root, trash_root))
        return TrashBatchResult(
            items=[
                TrashMoveResult(
                    record=record,
                    source=record.jsonl_path,
                    move_ok=True,
                    prune_ok=True,
                )
                for record in records
            ]
        )

    monkeypatch.setattr("codex_session_cleaner.app.trash_records", fake_trash_records)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()

            await pilot.press("d")
            await pilot.pause()

            assert len(app.screen_stack) == 2
            assert isinstance(app.screen, ConfirmationScreen)

            app.selected_record_paths.clear()
            app.selected_record_paths.add(bravo.jsonl_path)

            app.screen.action_confirm()
            await pilot.pause()

            assert len(cleanup_calls) == 1
            acted_records, session_root, trash_root = cleanup_calls[0]
            assert [record.jsonl_path for record in acted_records] == [alpha.jsonl_path]
            assert session_root == app.session_root
            assert trash_root.name == "sessions"
            assert "1 moved" in str(app.query_one("#status-line", Static).renderable)

    asyncio.run(scenario())


def test_cancel_and_escape_do_not_trigger_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    record = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(
        "codex_session_cleaner.app.discover_sessions",
        lambda session_root=None: [record],
    )

    cleanup_calls: list[tuple[tuple[SessionRecord, ...], Path, Path]] = []

    def fake_trash_records(records: list[SessionRecord], session_root: Path, trash_root: Path) -> TrashBatchResult:
        cleanup_calls.append((tuple(records), session_root, trash_root))
        return TrashBatchResult(
            items=[
                TrashMoveResult(
                    record=record,
                    source=record.jsonl_path,
                    move_ok=True,
                    prune_ok=True,
                )
            ]
        )

    monkeypatch.setattr("codex_session_cleaner.app.trash_records", fake_trash_records)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()

            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmationScreen)

            app.screen.action_cancel()
            await pilot.pause()
            assert cleanup_calls == []

            await pilot.press("d")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmationScreen)

            await pilot.press("escape")
            await pilot.pause()

            assert cleanup_calls == []
            assert len(app.screen_stack) == 1

    asyncio.run(scenario())


def test_successful_cleanup_refreshes_visible_list_and_removes_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    discovered: list[list[SessionRecord]] = [[alpha, bravo], [bravo]]

    def fake_discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
        return list(discovered.pop(0))

    monkeypatch.setattr("codex_session_cleaner.app.discover_sessions", fake_discover_sessions)

    cleanup_calls: list[tuple[tuple[SessionRecord, ...], Path, Path]] = []

    def fake_trash_records(records: list[SessionRecord], session_root: Path, trash_root: Path) -> TrashBatchResult:
        cleanup_calls.append((tuple(records), session_root, trash_root))
        return TrashBatchResult(
            items=[
                TrashMoveResult(
                    record=record,
                    source=record.jsonl_path,
                    move_ok=True,
                    prune_ok=True,
                )
                for record in records
            ]
        )

    monkeypatch.setattr("codex_session_cleaner.app.trash_records", fake_trash_records)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            app.screen.action_confirm()
            await pilot.pause()

            assert len(cleanup_calls) == 1
            assert [record.jsonl_path for record in cleanup_calls[0][0]] == [alpha.jsonl_path]
            assert [record.jsonl_path for record in app.visible_records] == [bravo.jsonl_path]
            assert app.selected_record_paths == set()
            assert "Cleanup" in str(app.query_one("#status-line", Static).renderable)
            assert "moved" in str(app.query_one("#status-line", Static).renderable)

    asyncio.run(scenario())


def test_r_refresh_reloads_sessions_from_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    discovered: list[list[SessionRecord]] = [[alpha], [alpha, bravo]]

    def fake_discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
        return list(discovered.pop(0))

    monkeypatch.setattr("codex_session_cleaner.app.discover_sessions", fake_discover_sessions)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            assert [record.jsonl_path for record in app.visible_records] == [alpha.jsonl_path]

            await pilot.press("r")
            await pilot.pause()

            assert [record.jsonl_path for record in app.visible_records] == [
                alpha.jsonl_path,
                bravo.jsonl_path,
            ]
            assert "r refresh" in str(app.query_one("#status-line", Static).renderable)

    asyncio.run(scenario())


def test_failed_cleanup_keeps_item_visible_and_surfaces_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    discovered: list[list[SessionRecord]] = [[alpha, bravo], [bravo]]

    def fake_discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
        return list(discovered.pop(0))

    monkeypatch.setattr("codex_session_cleaner.app.discover_sessions", fake_discover_sessions)

    cleanup_calls: list[tuple[tuple[SessionRecord, ...], Path, Path]] = []

    def fake_trash_records(records: list[SessionRecord], session_root: Path, trash_root: Path) -> TrashBatchResult:
        cleanup_calls.append((tuple(records), session_root, trash_root))
        return TrashBatchResult(
            items=[
                TrashMoveResult(
                    record=records[0],
                    source=records[0].jsonl_path,
                    move_ok=True,
                    prune_ok=True,
                ),
                TrashMoveResult(
                    record=records[1],
                    source=records[1].jsonl_path,
                    move_ok=False,
                    prune_ok=False,
                    move_error=RuntimeError("permission denied while moving session to trash"),
                ),
            ]
        )

    monkeypatch.setattr("codex_session_cleaner.app.trash_records", fake_trash_records)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()
            session_list.index = 1
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            app.screen.action_confirm()
            await pilot.pause()

            assert len(cleanup_calls) == 1
            assert [record.jsonl_path for record in cleanup_calls[0][0]] == [
                alpha.jsonl_path,
                bravo.jsonl_path,
            ]
            assert [record.jsonl_path for record in app.visible_records] == [bravo.jsonl_path]
            assert app.visible_records[0].warnings
            assert "permission denied while moving session to trash" in str(
                app.query_one("#details", Static).renderable
            )
            assert "1 failed" in str(app.query_one("#status-line", Static).renderable)

    asyncio.run(scenario())


def test_prune_failure_keeps_failed_item_visible_after_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    alpha = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-alpha.jsonl",
        "alpha123456",
        "/projects/alpha",
        datetime(2026, 3, 30, 1, 0, tzinfo=timezone.utc),
    )
    bravo = make_record(
        tmp_path / "sessions" / "2026" / "03" / "30" / "rollout-bravo.jsonl",
        "bravo123456",
        "/projects/bravo",
        datetime(2026, 3, 30, 2, 0, tzinfo=timezone.utc),
    )
    discovered: list[list[SessionRecord]] = [[alpha, bravo], [bravo]]

    def fake_discover_sessions(session_root: Path | None = None) -> list[SessionRecord]:
        return list(discovered.pop(0))

    monkeypatch.setattr("codex_session_cleaner.app.discover_sessions", fake_discover_sessions)

    def fake_trash_records(records: list[SessionRecord], session_root: Path, trash_root: Path) -> TrashBatchResult:
        return TrashBatchResult(
            items=[
                TrashMoveResult(
                    record=records[0],
                    source=records[0].jsonl_path,
                    move_ok=True,
                    prune_ok=False,
                    prune_error=RuntimeError("failed to prune empty session directories"),
                ),
                TrashMoveResult(
                    record=records[1],
                    source=records[1].jsonl_path,
                    move_ok=True,
                    prune_ok=True,
                ),
            ]
        )

    monkeypatch.setattr("codex_session_cleaner.app.trash_records", fake_trash_records)

    async def scenario() -> None:
        app = CodexSessionCleanerApp()
        app.session_root = tmp_path / "sessions"

        async with app.run_test() as pilot:
            await pilot.pause()

            session_list = app.query_one("#session-list", ListView)
            session_list.index = 0
            await pilot.pause()

            await pilot.press("space")
            await pilot.pause()
            session_list.index = 1
            await pilot.pause()
            await pilot.press("space")
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            app.screen.action_confirm()
            await pilot.pause()

            assert [record.jsonl_path for record in app.visible_records] == [
                alpha.jsonl_path,
            ]
            assert app._record_for_path(alpha.jsonl_path) is not None
            assert "failed to prune empty session directories" in str(
                app.query_one("#details", Static).renderable
            ) or any(
                "failed to prune empty session directories" in warning
                for warning in app._record_for_path(alpha.jsonl_path).warnings
            )
            assert "1 failed" in str(app.query_one("#status-line", Static).renderable)

    asyncio.run(scenario())
