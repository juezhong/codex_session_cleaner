# Codex Session Cleaner Design

## Overview

Build a lightweight terminal UI for reviewing and cleaning up local Codex sessions.
The tool scans the local Codex session store, shows each session with its working
directory, supports interactive filtering and multi-selection, and moves selected
session files into a trash directory instead of permanently deleting them.

This tool is intentionally narrow in scope. It is not a full session manager and
does not handle resume, rename, tagging, or automatic cleanup rules.

## Goals

- Show local Codex sessions discovered from the default on-disk session store.
- Display the session working directory (`cwd`) prominently so cleanup decisions
  can be made by inspection.
- Support fast interactive filtering and keyboard-driven multi-selection.
- Move selected session files into a trash directory while preserving their
  original directory hierarchy.
- Refresh the UI after cleanup and show clear success/failure results.

## Non-Goals

- Resuming sessions.
- Renaming or tagging sessions.
- Modifying Codex configuration or other metadata outside session files.
- Automatic deletion based on time, project, or heuristics.
- Building a general-purpose TUI framework for future tools.

## Environment Assumptions

- Codex stores sessions under `~/.codex/sessions/...`.
- If `CODEX_HOME` is set, the tool uses `$CODEX_HOME/sessions` instead.
- Individual sessions are represented by `rollout-*.jsonl` files under date-based
  directory trees such as `YYYY/MM/DD/`.
- Session metadata needed for display can be derived from each `jsonl` file,
  especially the `cwd` field and session identifier.

These assumptions are based on observed Codex behavior and public project
discussions. The tool should degrade safely if some files do not match the
expected structure.

## User Experience

The program is a single-screen TUI built with `textual`.

### Layout

- Left pane: scrollable session list.
- Right pane: details for the currently highlighted session.
- Top or bottom utility area: filter input, counters, and key hints.

### Session List

Each row represents one `rollout-*.jsonl` file and shows:

- selection marker
- shortened `cwd`
- last updated time
- short session ID

Rows are keyboard navigable. Users can toggle selection on one or many rows.

### Details Pane

The details pane shows the full metadata for the highlighted row:

- full `cwd`
- full session ID
- absolute `jsonl` path
- last updated time
- any parse warnings if the file is malformed or incomplete

### Filtering

The user can type a keyword filter that matches against:

- `cwd`
- session ID
- absolute session file path

Filtering only affects which rows are visible. It must not alter selection state
for rows that remain loaded in memory.

### Cleanup Flow

1. User filters and selects one or more sessions.
2. User triggers cleanup with a dedicated key binding.
3. The app opens a confirmation dialog showing:
   - selected count
   - each selected session's `cwd`
   - each selected session's short ID
4. On confirmation, the app moves the selected files to trash.
5. The app refreshes the list and shows per-item results.

## Data Model

The internal model treats one session file as one cleanup unit.

```text
SessionRecord
  session_id: str
  cwd: str | None
  jsonl_path: Path
  created_at: datetime | None
  updated_at: datetime | None
  display_label: str
  warnings: list[str]
```

### Why File-Based Units

Each visible row maps directly to one on-disk `rollout-*.jsonl` file. This keeps
cleanup semantics explicit and predictable:

- no grouping by `cwd`
- no grouping by date directory
- no inferred linkage to other files that happen to share a session ID

## Session Discovery

The scanner walks the session root and finds files matching `rollout-*.jsonl`.

For each file:

- read line by line and ignore lines that are not valid JSON objects
- collect candidate values for `session_id`, `cwd`, and timestamp fields from
  valid objects only
- set `session_id` to the first non-empty candidate encountered in file order
- set `cwd` to the first non-empty candidate encountered in file order
- inspect timestamp candidates in this exact key order:
  - `timestamp`
  - `created_at`
  - `updated_at`
  - `time`
- for each valid JSON object, use at most the first parseable value found from
  that key order as that record's event timestamp
- set `created_at` to the minimum event timestamp across all parsed records
- set `updated_at` to the maximum event timestamp across all parsed records
- if no valid timestamp is found, fall back to filesystem metadata such as mtime
- if later records disagree with earlier `session_id` or `cwd` values, keep the
  first accepted value and append a warning describing the conflict

Malformed files are still surfaced in the UI as long as the file exists. Missing
fields are shown as unknown rather than silently skipping the file.

## Trash Semantics

Cleanup means moving files, not deleting them permanently.

### Source and Destination

- source root: `~/.codex/sessions` or `$CODEX_HOME/sessions`
- trash root: `~/.codex/trash/sessions` or `$CODEX_HOME/trash/sessions`

The relative path under `sessions/` is preserved exactly under `trash/sessions/`.

Example:

```text
~/.codex/sessions/2026/03/30/rollout-abc.jsonl
-> ~/.codex/trash/sessions/2026/03/30/rollout-abc.jsonl
```

### Collision Handling

If the destination path already exists, the tool must not overwrite it silently.
Instead, it appends a deterministic suffix before the `.jsonl` extension using
this rule:

1. If a non-empty session ID is available, append `.<session_id[:8]>`.
2. If that destination also exists, append `.<session_id[:8]>.dupN`, where `N`
   starts at `1` and increments until a free path is found.
3. If no session ID is available, use `.unknown.dupN` with the same incrementing
   rule.

Examples:

```text
rollout-abc.jsonl
-> rollout-abc.<sessionid8>.jsonl
-> rollout-abc.<sessionid8>.dup1.jsonl
```

### Empty Directories

After moving a file, the app should remove newly empty directories within the
source session tree, walking upward until it reaches either a non-empty
directory or the session root. It must not remove non-empty directories and must
not touch content outside the session root.

## Error Handling

- If a session file cannot be parsed, show it with warnings and allow cleanup.
- If destination parent directories do not exist, create them.
- If moving one selected file fails, continue processing the remaining files.
- After a cleanup action, show a result summary with successful and failed items.
- If scanning finds no session files, show an empty state instead of failing.

## Key Interactions

Exact key bindings may be adjusted during implementation, but the intended model
is:

- `Up` / `Down`: move highlight
- `Space`: toggle row selection
- `/` or equivalent: focus filter input
- `d`: open cleanup confirmation
- `q`: quit

## Architecture

Keep the implementation split into a few focused modules even if the first
delivery is still small:

- session discovery and parsing
- trash-path planning and move operations
- `textual` application state and widgets

The parser and path-mapping logic should remain testable without the TUI.

## Testing Strategy

### Unit-Level Coverage

Test pure logic around:

- discovering `rollout-*.jsonl` files
- extracting `cwd` and session ID from representative JSONL inputs
- generating trash destination paths
- handling destination collisions

### Manual Integration Coverage

Verify the running TUI can:

- render a session list
- filter by `cwd` and session ID
- multi-select rows
- confirm cleanup
- move files into trash with preserved hierarchy
- refresh after cleanup

### Failure Cases

Verify behavior for:

- malformed JSONL content
- missing `cwd`
- existing destination file in trash
- partial move failures
- no sessions found

## Implementation Boundary

The first implementation should stop once the cleanup loop is solid:

- scan
- display
- filter
- multi-select
- confirm
- move to trash
- refresh with result feedback

Anything beyond that, such as restore flows, richer previews, or resume support,
belongs to later work.
