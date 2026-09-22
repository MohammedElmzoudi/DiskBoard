# DiskBoard updates

DiskBoard is free and uses the MIT license. Agent service fees are separate.

Use a Git installation to get automatic updates:

```sh
git clone https://github.com/MohammedElmzoudi/DiskBoard.git DiskBoard
cd DiskBoard
./install.sh
```

Run `diskboard` (or `~/.local/bin/diskboard`). On a normal interactive launch, it checks the official repository and applies a fast-forward update before the app loads. Close other DiskBoard windows first. Use `diskboard check-updates` to check without installing, or `diskboard update` to install now. Network errors do not stop the app.

Updates skip checkouts with local changes, an unexpected origin, or a branch that does not track the matching origin branch. A divergent branch cannot update. No reset, force checkout, or cleanup command is used. Git hooks are disabled for update commands. Existing cleanup settings stay in their original diskpick folders.

A ZIP download does not auto-update. Use the Git installation above for updates. Keep any existing folder with local edits; do not overwrite it. The GitHub repository name stays `diskpick` for compatibility.

Only run updates in a checkout that you control. Close editors that can change its files during an update. Updates download and run code from the official repository; review that trust before installation. Auto-checks run on a plain interactive launch, not during scans, cleanup, or an agent session.
