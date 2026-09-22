# Agent reports

## Use the app

1. Run `diskpick`. Choose Claude or Codex.
2. Select **Copy a new scan prompt** in the top pane.
3. Press **Shift+Down**. Paste the prompt into the agent. Send it.
4. Wait for the report. The top pane updates when it receives a complete report.
5. Use the arrow keys to move. Press Enter to open a group or read an item.
6. Press Space to select a CHECK item. On a group, Space selects its CHECK items.
7. Select **Check and review selected paths**. Read the exact paths. Confirm removal only if you want to remove them.

The prompt asks for ASD-STE100 Simplified Technical English. It asks for one short sentence per description. diskpick checks the data format, not formal compliance with the ASD-STE100 dictionary.

## What the states mean

- **CHECK:** The agent named a local rule. diskpick must still check the path.
- **INSPECT:** You can read the advice. This report cannot remove the path.
- **KEEP:** The agent identified important or active data. This report cannot remove the path.
- **Not measured:** The scan has no complete size. This does not mean zero bytes.

Sizes in a report are agent estimates. A group with an incomplete size shows the known total plus `unmeasured`. The free-space counter comes from the local disk. Free space can change while other apps run.

## Scan prompt

`diskpick prompt` prints a sample prompt with a new scan ID. Use the **Copy** button for a prompt linked to the open pane.

The prompt starts with `diskpick rules --json`. This lists local rules without a full size scan. The agent then measures broad folders and inspects the five largest areas. It must report scan errors and avoid repeated full-disk scans. It must not delete files, stop processes, or change the app during the scan. These are instructions to the agent, not an operating-system sandbox. Your CLI keeps its normal permission controls. Use its read-only mode if you require enforced read-only access.

The agent uses its existing account. Its provider receives the prompt and tool results under that CLI's normal settings. diskpick adds no API proxy and does not upload reports itself. It does not save raw terminal output to a file. The dedicated tmux session keeps a bounded terminal history until the session ends.

## Report contract, version 1

For a robust final reply, pass the JSON below to `diskpick frame` on standard input. Copy its complete output into the final reply. The helper encodes JSON as base64 so hard line wrapping cannot change paths. It validates the data and does not write files. Raw JSON frames remain supported when the CLI preserves them exactly.

A raw JSON frame has these lines. Replace `SCAN_ID` with the ID in the copied prompt. Put only one complete report in the final reply.

```text
DISKPICK_REPORT_BEGIN SCAN_ID
{"version":1,"scan_id":"SCAN_ID","summary":"State the main disk use and scan gaps.","groups":[{"title":"Build files","description":"These folders contain generated files.","items":[{"id":"build-1","title":"Old build files","path":"/demo/project/target","bytes":1073741824,"description":"This folder contains files from earlier builds.","regeneration":"The build command creates these files again.","risk":"review","evidence":"No local safety check has run.","area_id":null}]}]}
DISKPICK_REPORT_END SCAN_ID
```

The example path is synthetic. Never return example data for a real scan.

- `groups` has at most 30 entries. The report has at most 300 items and 256 KiB of JSON.
- Each item has a unique `id` and an exact absolute `path`.
- Paths must not overlap. List a parent or its children, not both.
- `bytes` is a measured count of allocated bytes, or `null` if incomplete.
- `risk` is `rebuildable`, `review`, or `keep`. It is agent advice.
- `area_id` is an existing local rule ID, or `null`. It cannot define a new rule.
- `description`, `regeneration`, and `evidence` explain the contents, restore method, and basis for the advice.
- No extra fields, shell commands, terminal control characters, or duplicate JSON keys are accepted.

## Receiving replies

The top pane reads the lower pane once per second. tmux joins wrapped terminal lines before parsing. The reader accepts ordinary indentation, common Claude/Codex bullets, and vertical text borders. BASE64 frames tolerate inserted whitespace without changing decoded paths. Raw JSON with newlines inside strings is rejected; paths are never guessed. It reads at most 8,000 terminal lines and parses a bounded tail. It does not inspect unrelated panes.

Each copied prompt has a new scan ID. Replies for other scans cannot replace it. Only the last started frame for that scan can load. Partial or invalid frames do not replace a valid report. Extra terminal output does not replay a report or clear the selection.

This is a terminal-text protocol, not an authenticated assistant-message API. Text printed by a tool can also contain a frame. A CLI can hide or truncate its output. If a report does not load, open **Read scan notes**. Ask the agent to use `diskpick frame` and print the complete encoded frame in its final reply. Reloading the top pane starts a new reader; copy a new prompt after a reload. Reports are not saved across app restarts.

## Removal boundary

The report never becomes executable code or a cleanup configuration. diskpick discovers its local rules again, checks the selected rules, and intersects their eligible preview paths with the selected report paths. It shows that exact set before confirmation. The existing cleaner checks those paths again and writes an audit record. A later scan cannot add paths to the confirmed set.

An unsupported path stays inspect-only even if the agent calls it safe. Add support through a reviewed handler and fixture tests; see [EXTENDING.md](EXTENDING.md). Worktree checks, protected base checkouts, recent work, ignored files, uncommitted changes, and active-process guards remain in the existing cleaner. No agent report can override them.
