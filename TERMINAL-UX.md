# DiskBoard terminal UX rules

This is the design and acceptance contract for DiskBoard's terminal interface.
It records requirements, not a claim that the current release meets all of them.
Read it before changing screens, controls, scan behavior, or cleanup flows.

## Sources and attribution

These are primary sources from people building established command-line tools.
Sources reviewed September 2026. The summaries are paraphrases; DiskBoard-specific
requirements are our application of the guidance, not quotations or endorsements.

- **Command Line Interface Guidelines**, by Aanand Prasad and Ben Firshman
  (co-creators of Docker Compose), Carl Tashian, and Eva Parish:
  [authors and guide](https://clig.dev/#authors),
  [discoverability](https://clig.dev/#ease-of-discovery),
  [arguments and confirmation](https://clig.dev/#arguments-and-flags), and
  [responsiveness](https://clig.dev/#robustness).
  Design around people; expose help and next steps; acknowledge input promptly;
  show ongoing work; let users preview consequential changes and confirm them.
  The guide explicitly excludes full-screen applications from its scope. Its
  general CLI principles inform DiskBoard, but it is not a curses layout manual.
- **GitHub CLI's Primer guidelines**, in the `cli/cli` repository:
  [principles](https://github.com/cli/cli/blob/trunk/docs/primer/getting-started/README.md)
  and [foundations](https://github.com/cli/cli/blob/trunk/docs/primer/foundations/README.md).
  Choose useful defaults, reduce what users must remember, use consistent words
  and behavior, and establish hierarchy with spacing and emphasis. Machine output
  needs explicit state, complete values, and freedom from terminal decoration.
- **Junegunn Choi's fzf**, as documented in its own repository:
  [finder controls](https://github.com/junegunn/fzf#using-the-finder),
  [reload](https://github.com/junegunn/fzf#reloading-the-candidate-list), and
  [preview](https://github.com/junegunn/fzf#preview-window).
  This is a concrete interaction reference, not a universal style guide: it
  documents keyboard selection, cancellation, mouse interaction, selection-linked
  previews, and visible hints for refreshing a list. Borrow the coherence of the
  interaction, without requiring fzf or copying every shortcut.
- **Evil Martians, CLI UX best practices**:
  [three patterns for progress displays](https://evilmartians.com/chronicles/cli-ux-best-practices-3-patterns-for-improving-progress-displays).
  Supplementary specialist guidance: choose progress indicators appropriate to
  the work and leave a clear completed result instead of a stale running label.

## The main job

Open on **Cleanup groups**. The primary task is:

> Delete files older than 10 days in these folders.

A group stores a name, one or more explicitly chosen folders, and an editable age
rule. Present it as a sentence: **Files last modified more than 10 days ago ·
3 folders**. Define age as modification time, not proof that a file is unused.
Show whether subfolders are included. A folder's timestamp alone must never
qualify all of its contents for deletion.

Storage exploration is a second view with an obvious return to groups. Opening
the app, adding a folder, changing a filter, and measuring space never delete data.
Existing size-only groups remain read-only until the user explicitly configures
a cleanup rule.

## Required interactions

| Interaction | Required observable result |
|---|---|
| Open DiskBoard | Saved cleanup groups appear first, with Add group and Storage visible. |
| Add or edit a group | Choose folders and the age rule; show the resulting sentence before saving. Cancel preserves the prior group. |
| Track a storage folder | Open group configuration, or show a persistent success message naming the saved group. Duplicates, unsupported rows, and errors explain what happened. |
| Select a group | Show its folders, rule, candidate count and eligible size, or the current inspection state. |
| Review cleanup | Show exact matching files, exclusions, count, size, and rule. Backing out changes nothing. |
| Delete reviewed files | Require separate explicit confirmation naming the scope. Recheck reviewed candidates; skip changed or unsafe files and explain why. |
| Remove group | Say “Remove group”; remove only its saved configuration. This label never means deleting its files. |
| Finish cleanup | Report deleted and skipped counts, reasons, and audit location. Refresh the group. Partial success must not imply everything was removed. |
| Unavailable action | Explain “No files match”, “Select a folder”, or the specific error. An advertised shortcut must never silently do nothing. |

Keep selection stable while measurements update or rows reorder. Make focus
visible. Display current-screen shortcuts and expose full help on demand. Enter
opens/accepts, Escape backs out/cancels, arrows navigate, and selection shortcuts
have consistent meanings. The whole workflow must work with the keyboard. Mouse
actions must produce the same results as their keyboard equivalents.

## Honest, responsive storage scans

- Distinguish scanning, paused, completed, cancelled, and failed. During work,
  show changing evidence such as entries inspected and elapsed time. A spinner
  alone does not establish that more data has been measured.
- Render useful partial results while input remains responsive. Stop owned scan
  work on exit. Slow filesystem operations must not freeze navigation.
- Label partial measurements **12 GiB found so far**, with a continuation action.
  Do not make a bare `>=12` carry the explanation.
- Name the denominator of every percentage. A folder's share of measured bytes
  is not percentage of the disk scanned. Never invent completion or extrapolate
  accurate folder sizes from display depth.
- Prefer opening a folder to a mysterious depth slider. If depth exists, label
  it **Visible levels** and explain that it changes the drawing. Scan effort
  controls inspection work and must show its limit and resulting state.
- Paused scans need **Continue scanning**. Resource limits need an explanation
  and a usable action, such as scanning the selected folder alone. A maximum
  setting must not imply that the scan is complete.

## Clear terminal presentation

Use terminal foreground/background colors, visible focus, sentence-case labels,
and whitespace. Never depend on color alone. Keep the list compact; place full
paths and explanations in a readable detail or preview view. Narrow and resized
layouts retain the primary action, selection state, cancellation, and access to
full paths.

Provide CLI help and noninteractive output alongside the TUI. Preserve JSON
contracts, meaningful exit statuses, and explicit noninteractive cleanup
selection/confirmation. Redirected machine output must not contain curses
escapes or animations. User data must not become terminal control sequences.

## Acceptance before release

Use disposable directories and an isolated HOME. Never test deletion against a
personal folder. Record an actual terminal session for these flows:

1. Create a group containing two folders with old and recent fixture files.
   Set 10 days, restart, and verify that folders and rule persist.
2. Review matching old files; cancel and verify every file remains.
3. Confirm the same review; only eligible reviewed files are removed. Recent,
   excluded, changed-after-preview, linked, and uninspected files remain.
4. Test an empty group, failed inspection, and duplicate tracked folder. Each
   state has visible feedback and a recoverable next action.
5. Track from Storage, return to groups, and find the result. Test both the
   shortcut and mouse action where supported.
6. Observe changing scan results, pause, continue, open a folder, and quit during
   scanning. Verify partial values and display-depth controls are understandable.
7. Complete the primary flow with only the keyboard; check a narrow terminal
   and terminal colors. Inspect rendered captures as well as assertions.
8. Exercise the packaged download and installer separately. Unit tests and a
   source checkout do not prove that the downloaded app works.

Record what passed on which platform. Adding this document does not clear these
release criteria; implementation and acceptance evidence are still required.
