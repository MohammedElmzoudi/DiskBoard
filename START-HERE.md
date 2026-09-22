# Start DiskBoard

This folder contains DiskBoard for macOS. Keep the folder intact.

## Before you start

- Install Python 3.9 or later. This package does not bundle Python.
- Split panes need tmux and Claude Code or Codex CLI. You need your own agent account; provider charges or usage limits can apply.
- Cleanup supports macOS only. Windows cleanup is not supported.
- This is a source-based terminal tool, not a signed or notarized native Mac app.

## Open the app

Open Terminal in this folder and run:

```sh
sh './Start DiskBoard.command'
```

You can also open `Start DiskBoard.command` from Finder when macOS permits it. Do not disable Gatekeeper to open the tool. If macOS blocks the script, use Terminal after reviewing the source and the checksum.

For the short `diskboard` command, run `sh install.sh`. The installer creates one launcher in `~/.local/bin`. It does not edit PATH or replace a different existing launcher. If that folder is not on PATH, run `~/.local/bin/diskboard` directly. Keep this downloaded folder in its chosen location.

## Make a report

1. Choose Claude or Codex. If DiskBoard finds an installed CLI outside PATH, choose Yes to save its location or Use once.
2. Select Copy a new scan prompt.
3. Press Shift+Down. Paste and send the prompt in the agent pane.
4. Wait for the report. Expand groups with Enter. Use Space to select CHECK items.
5. Read the local check and exact path preview before you confirm removal.

INSPECT and KEEP items cannot be removed from the report. Agent advice does not bypass the cleaner. The tool does not guarantee a particular amount of free space.

## Common problems

- **Login expired:** sign in through your chosen CLI. DiskBoard does not store its credentials.
- **CLI not found:** choose an installed executable or open the official install guide from setup. Local cleanup tools remain available without an agent.
- **Report did not load:** ask the agent to use the local `diskboard frame` command named in the prompt and print the full encoded result. Do not manually repair paths.
- **Nothing can be removed:** read the local safety reason. Active work, new data, and unsupported paths are kept.
- **You closed the window:** detached tmux workspaces can keep the agent running. Use the resume command printed by DiskBoard. Exit the agent when you want it to stop.

Ctrl+b then r reloads the top pane. Ctrl+b then d detaches. Shift+Up and Shift+Down change focus.

## Support and license

Email mohammedelmzo@gmail.com with the version and error text. Do not send credentials, login codes, raw agent conversations, or a full personal disk inventory.

The included MIT license applies to the source. The Free download package does not include an agent subscription or a BentoBoard license. See REPORTS.md for the report format and safety limits.

Read [UPDATES.md](UPDATES.md) for automatic updates with a Git installation. ZIP copies do not auto-update.
