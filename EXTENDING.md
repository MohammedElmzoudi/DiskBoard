# Extending diskpick

## Adding an area with an existing handler

Edit your private `~/.config/diskpick/areas.json`, or the shipped `areas.json` when contributing a general default. Each entry needs a unique `id`, printable `title` and `description`, and an approved `kind`. Remove an entry to remove its option. Reorder entries to reorder the picker; stable IDs do not change.

`inspect` accepts absolute or home-relative paths and never deletes. `rust` accepts conventional paths ending in `target/debug` with an adjacent project `Cargo.toml`. `browser` only selects one of the three fixed browser-cache names. JSON never contains shell commands, Python expressions, or unrestricted deletion paths.

Do not ship machine-specific paths, private inventories, audit logs, credentials, or user content. Local catalogs live outside the repository.

## Adding a new cleanup handler

1. Document which tool owns the data and why each selected file is regenerable. Prefer the owning tool's supported maintenance API where it provides locking. Age alone does not prove disposability.
2. Add a named handler to `catalog.validate`, `roots`, and `execute_handler`. Keep inventory separate from cleanup. Return unknown for inaccessible/incomplete inventories.
3. Reuse `engine` descriptor traversal, audit, snapshot and activity guards. Never weaken them to make an uncertain directory pass.
4. Define concurrency behavior: locks, active-process detection, recent-file rules, and what happens if another process writes mid-run. If reliable coordination is unavailable, make it inspect-only.
5. Add disposable-fixture tests proving exact scope, preservation of neighboring important data, dry-run behavior, changed-file handling, symlink/mount rejection, busy locks, missing permissions and audit failure. Add CLI selection tests.
6. Update the README deletion table. Keep approval-sensitive media/checkpoints, source, databases and worktrees out of generic age-based deletion.

There is no plugin interface that imports executable code from arbitrary user JSON. Adding a new destructive strategy requires a reviewed code change.
