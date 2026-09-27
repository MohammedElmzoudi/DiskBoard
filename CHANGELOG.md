# DiskBoard 0.1.0

- Open directly to a bounded, read-only storage tree. Zoom into folders, change visible depth, pause, and continue with a larger scan budget.
- See allocated bytes and shares of the data found so far. Partial sizes are labeled; unknown folders are never presented as empty.
- Keep one saved list of cleanup groups. Add size-only folders, rename, remove from the list, undo, and filter. Review exact eligible paths before a separate removal confirmation.
- Restore a timestamped storage snapshot while a fresh quick scan runs. Measurement processes stop when their view closes.
- Preserve the published official and legacy Git update URLs and executable command installation.

macOS, Python 3.9+. Agents and tmux are optional. Source ZIPs do not auto-update.
Storage totals can overlap APFS shared allocation. A partial scan is not a full disk inventory or a promise of reclaimable space.
