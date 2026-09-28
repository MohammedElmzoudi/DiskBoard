# DiskBoard 0.2.0

- Start on Cleanup groups. Save multiple folders with an editable 10-day age filter and subfolder setting.
- Review exact matching files, cancel safely, then explicitly confirm batch deletion. Changed, linked, active and uninspected files are kept; group folders remain.
- Track opens group configuration and then shows the saved group. File rows explain why a folder must be selected.
- Storage shows partial sizes as “so far”, labels visible levels separately from scan effort, and offers a focused folder scan.
- Include sourced terminal UX rules and concrete acceptance criteria in TERMINAL-UX.md.

Age rules use modification time, not last use. Cleanup remains macOS-only and requires review. The source ZIP requires Python 3.9+ and is unsigned.

# DiskBoard 0.1.0

- Open directly to a bounded, read-only storage tree. Zoom into folders, change visible depth, pause, and continue with a larger scan budget.
- See allocated bytes and shares of the data found so far. Partial sizes are labeled; unknown folders are never presented as empty.
- Keep one saved list of cleanup groups. Add size-only folders, rename, remove from the list, undo, and filter. Review exact eligible paths before a separate removal confirmation.
- Restore a timestamped storage snapshot while a fresh quick scan runs. Measurement processes stop when their view closes.
- Preserve the published official and legacy Git update URLs and executable command installation.

macOS, Python 3.9+. Agents and tmux are optional. Source ZIPs do not auto-update.
Storage totals can overlap APFS shared allocation. A partial scan is not a full disk inventory or a promise of reclaimable space.
