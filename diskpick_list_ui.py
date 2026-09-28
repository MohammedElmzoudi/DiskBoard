"""One saved list, with contextual details and an explicit cleanup review."""
import curses
import queue
import json
import os
import signal
import subprocess
import sys
import shutil
import threading
import textwrap
from pathlib import Path

import diskpick_catalog as catalog
import diskpick_engine as engine
import diskpick_ui as ui
from diskpick_list import SavedList, folder_area, cleanup_area


def clip(value, width):
    value = ui.safe(value)
    return value if len(value) <= width else value[:max(0, width - 1)] + '…'


def short_path(value):
    value = str(value)
    home = str(Path.home())
    return '~' + value[len(home):] if value == home or value.startswith(home + '/') else value


def removable(row):
    return bool(row and row['area']['kind'] != 'inspect' and row['total_bytes'] is not None
                and row.get('preview'))


def state_label(row):
    if row is None:
        return 'Checking…'
    if row['total_bytes'] is None:
        return 'Check failed'
    if row['area']['kind'] == 'inspect':
        return 'Size only'
    if removable(row):
        if row['area']['kind'] == 'aged':
            count = len(row['preview'])
            return '%d %s · %s' % (count, 'file' if count == 1 else 'files', ui.amount(row['eligible_bytes']))
        return ui.amount(row['eligible_bytes']) + ' to review'
    if row.get('status') == 'SETUP':
        return 'Needs a path'
    return 'Empty' if row['total_bytes'] == 0 else 'Kept'


class Measurements:
    """Stream each result without blocking the list, input or exit."""
    def __init__(self, areas, scan, isolate=False):
        self.process = None
        self.rows = {}
        self.events = queue.Queue()
        self.pending = {a['id'] for a in areas}
        if isolate and areas:
            # macOS: a private process group lets exit stop only our read-only
            # du/Git/lsof children, including nested executor threads.
            self.process = subprocess.Popen([sys.executable, '-B', str(Path(__file__)), '--measure'],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.DEVNULL, text=True, start_new_session=True)
            self.process.stdin.write(json.dumps(areas))
            self.process.stdin.close()
            def receive():
                remaining = {a['id']: a for a in areas}
                try:
                    for line in self.process.stdout:
                        row = json.loads(line)
                        key = row['area']['id']
                        if key in remaining:
                            self.events.put((key, row))
                            remaining.pop(key)
                except (OSError, ValueError, KeyError):
                    pass
                finally:
                    self.process.stdout.close()
                    for key, area in remaining.items():
                        self.events.put((key, dict(area=area, total_bytes=None, eligible_bytes=0,
                            preview=[], paths=[], reasons=['Measurement stopped. Refresh to try again.'], status='SKIPPED')))
            threading.Thread(target=receive, daemon=True).start()
            return
        work = queue.Queue()
        for area in areas:
            work.put(area)
        def worker():
            while True:
                try:
                    area = work.get_nowait()
                except queue.Empty:
                    return
                try:
                    result = scan([area], True)
                    if len(result) != 1 or result[0]['area']['id'] != area['id']:
                        raise ValueError('The measurement did not return this item.')
                    row = result[0]
                except Exception as exc:
                    row = dict(area=area, total_bytes=None, eligible_bytes=0,
                               preview=[], paths=[], reasons=[str(exc)], status='SKIPPED')
                self.events.put((area['id'], row))
        for _ in range(min(4, len(areas))):
            threading.Thread(target=worker, daemon=True).start()

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                try:os.killpg(self.process.pid, signal.SIGTERM)
                except ProcessLookupError:pass
                try:self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:os.killpg(self.process.pid, signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.process.wait(timeout=1)

    def poll(self):
        while True:
            try:
                key, row = self.events.get_nowait()
            except queue.Empty:
                return
            self.rows[key] = row
            self.pending.discard(key)


def edit_text(screen, title, label, initial='', hint=''):
    """Cancellable text entry, including quoted paths pasted by the terminal."""
    value = initial
    cursor = len(value)
    win = screen.win
    win.timeout(-1)
    curses.curs_set(1)
    try:
        while True:
            win.erase()
            h, w = win.getmaxyx()
            if h < 10 or w < 40:
                screen.line(0, 'Enlarge the terminal. Esc cancels.')
                win.refresh()
                if win.getch() == 27:
                    return None
                continue
            screen.line(1, title, curses.A_BOLD)
            screen.line(3, hint)
            screen.line(5, label, curses.A_BOLD)
            width = w - 6
            offset = max(0, cursor - width + 1)
            screen.line(6, value[offset:offset + width].ljust(width), curses.A_UNDERLINE)
            screen.line(h - 2, 'Enter  Save    Esc  Cancel    Ctrl+U  Clear')
            win.move(6, 2 + cursor - offset)
            win.refresh()
            key = win.get_wch()
            if key in ('\n', '\r', curses.KEY_ENTER):
                return value.strip() or None
            if key == '\x1b':
                return None
            if key == '\x15':
                value = ''; cursor = 0
            elif key in ('\x7f', '\b', curses.KEY_BACKSPACE):
                if cursor:
                    value = value[:cursor - 1] + value[cursor:]; cursor -= 1
            elif key == curses.KEY_DC:
                value = value[:cursor] + value[cursor + 1:]
            elif key == curses.KEY_LEFT:
                cursor = max(0, cursor - 1)
            elif key == curses.KEY_RIGHT:
                cursor = min(len(value), cursor + 1)
            elif key in (curses.KEY_HOME, '\x01'):
                cursor = 0
            elif key in (curses.KEY_END, '\x05'):
                cursor = len(value)
            elif isinstance(key, str) and key.isprintable() and len(value) < 4096:
                value = value[:cursor] + key + value[cursor:]; cursor += 1
    finally:
        curses.curs_set(0)


def document(screen, title, paragraphs, action=None):
    """Scrollable readable details, without turning each line into a menu item."""
    offset = 0
    win = screen.win
    win.timeout(-1)
    while True:
        win.erase()
        h, w = win.getmaxyx()
        lines = []
        for paragraph in paragraphs:
            lines.extend(textwrap.wrap(ui.safe(paragraph), max(12, w - 6)) or [''])
        room = max(1, h - 8)
        offset = max(0, min(offset, len(lines) - room))
        screen.line(1, title, curses.A_BOLD)
        screen.line(3, '─' * max(0, w - 4))
        for y, line in enumerate(lines[offset:offset + room], 4):
            screen.line(y, line)
        screen.line(h - 3, ('R  ' + action + '    ' if action else '') + 'Esc  Back', curses.A_BOLD)
        if len(lines) > room:
            screen.line(h - 2, '↑ ↓  Scroll    %d–%d of %d lines' % (offset + 1, min(offset + room, len(lines)), len(lines)))
        win.refresh()
        key = win.getch()
        if key == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
                if state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED) and y == h - 3:
                    if action and x < len(action) + 7:return True
                    return False
            except curses.error:pass
        if key in (27, ord('q')):
            return False
        if action and key in (ord('r'), ord('R')):
            return True
        if key == curses.KEY_DOWN: offset += 1
        elif key == curses.KEY_UP: offset -= 1
        elif key == curses.KEY_NPAGE: offset += room
        elif key == curses.KEY_PPAGE: offset -= room
        elif key == curses.KEY_HOME: offset = 0
        elif key == curses.KEY_END: offset = max(0, len(lines) - room)


def details(screen, area, row):
    lines = [area['description'], '']
    if row is None:
        lines += ['Still checking this item. Return to the list to see its size.']
    else:
        lines += ['On disk: ' + ('Unavailable' if row['total_bytes'] is None else ui.amount(row['total_bytes'])),
                  'Cleanup: ' + state_label(row), '']
        lines += [short_path(p['path']) + '  ·  ' + ui.amount(p['bytes']) for p in row.get('paths', [])]
        if row.get('reasons'):
            lines += ['', 'Why files are kept:'] + list(dict.fromkeys(row['reasons']))
    if not row or not row.get('paths'):
        lines += [short_path(p) for p in area.get('roots', [])]
    if area['kind'] == 'inspect':
        lines += ['', 'This item measures size only. Removing it from your list leaves its files in place.']
    document(screen, area['title'], lines)


def review_selected(screen, chosen, scan, clean, demo=False):
    if demo:
        fresh = demo_scan(chosen, True)
    else:
        fresh = screen.wait('Checking selected items', lambda: scan(chosen, True))
    ready = [row for row in fresh if removable(row)]
    paths = {p['path']: p for row in ready for p in row.get('preview', [])}
    if not paths:
        document(screen, 'Nothing to remove', ['The selected items have no eligible paths.', ''] +
                 [reason for row in fresh for reason in row.get('reasons', [])])
        return False
    paragraphs = ['Delete only the reviewed files below. This is permanent; DiskBoard does not move files to Trash.']
    if any(row['area']['kind'] == 'worktree' for row in ready):
        paragraphs.append('Worktree removal keeps the Git branch, commits and recovery reference.')
    paragraphs.append('')
    for row in fresh:
        paragraphs += [row['area']['title'] + ' · ' + state_label(row), row['area']['description']]
        paragraphs += [p['path'] + ' · ' + ui.amount(p.get('allocated_bytes', 0)) for p in row.get('preview', [])] if removable(row) else row.get('reasons', [])
        paragraphs.append('')
    paragraphs += ['Sizes can overlap and do not predict physical space recovered.']
    if not document(screen, 'Review exact paths', paragraphs, 'Continue to confirmation'):
        return False
    if screen.menu('Remove reviewed files?', '%d exact paths · active or changed files will be kept' % len(paths),
                   ['Cancel — keep files', 'Remove reviewed files']) != 1:
        return False
    if demo:
        document(screen, 'Demo complete', ['The review is complete. No real files were scanned or removed.'])
        return False
    approval = paths if any(r['area']['kind'] == 'aged' for r in ready) else set(paths)
    result = screen.wait('Removing reviewed files',
                         lambda: clean([r['area'] for r in ready], approval), mutating=True)
    completed = sum(item.get('completed', 0) for item in result['results'])
    reasons = [reason for item in result['results'] for reason in item.get('reasons', [])]
    document(screen, 'Review complete', ['%d cleanup operations completed.' % completed,
             'Observed free-space change: ' + ui.amount(result['net_change_bytes']),
             'Audit: ' + result['audit'], ''] + reasons)
    return True


def demo_areas():
    return [dict(id='downloads-demo', title='Downloads and exports', kind='aged', roots=['/demo/Downloads', '/demo/Exports'], older_than_days=10, recursive=True, description='Files last modified more than 10 days ago. Includes subfolders.')] + [dict(id=key, title=title, kind=kind, roots=[path], description=description)
            for key, title, kind, path, description in [
                ('compiler', 'Studio · compiler cache', 'rust', '/demo/Studio/target/debug', 'Older compiler sessions. The newest session and active builds stay.'),
                ('exports', 'Video exports', 'inspect', '/demo/Studio/exports', 'Final videos and review captures. Size only; files stay in place.'),
                ('projects', 'Projects', 'inspect', '/demo/Projects', 'Your source folders. Size only; files stay in place.'),
                ('worktree', 'Search experiment', 'worktree', '/demo/Studio/.claude/worktrees/search', 'An old linked checkout. Changed, active or untracked files block removal.'),
                ('archives', 'Archives', 'inspect', '/demo/Archives', 'Archived projects. Size only; files stay in place.')]]


def demo_scan(areas, quiet=True):
    values = {'downloads-demo': (16.8, 9.2), 'compiler': (4.6, 1.8), 'exports': (12.4, 0), 'projects': (8.2, 0),
              'worktree': (2.1, 0), 'archives': (6.8, 0)}
    rows = []
    for area in areas:
        total, eligible = values.get(area['id'], (.4, 0))
        path = area.get('roots', ['/demo/cache'])[0]
        rows.append(dict(area=area, total_bytes=int(total * engine.GIB), eligible_bytes=int(eligible * engine.GIB),
                         status='READY' if eligible else 'KEPT', candidates=int(bool(eligible)),
                         paths=[dict(path=path, bytes=int(total * engine.GIB))],
                         reasons=['Active process holds this checkout open.'] if area['id'] == 'worktree' else [],
                         preview=[dict(path=path + ('/old-export.zip' if area['kind'] == 'aged' else '/incremental/old-session'), allocated_bytes=int(eligible * engine.GIB))] if eligible else []))
    return rows


def edit_group(screen, saved, area=None, initial_root=None, demo=False):
    roots = list(area.get('roots', [])) if area else ([initial_root] if initial_root else [])
    name = area['title'] if area else (Path(initial_root).name if initial_root else '')
    if not name:
        name = edit_text(screen, 'New cleanup group', 'Group name', hint='Example: Downloads and exports')
        if not name:return None
    days = area.get('older_than_days', 10) if area else 10
    recursive = area.get('recursive', True) if area else True
    while True:
        labels = ['Save group', 'Add folder', 'Older than: %d days' % days,
                  'Include subfolders: ' + ('Yes' if recursive else 'No'), 'Rename group']
        labels += ['Remove folder: ' + short_path(p) for p in roots]
        labels += ['Cancel']
        choice = screen.menu(name, 'Files last modified more than %d days ago · %d folders. Saving deletes nothing.' % (days, len(roots)), labels)
        if choice is None or choice == len(labels) - 1:return None
        if choice == 0:
            if not roots:
                document(screen, 'Add a folder first', ['Choose at least one folder for this group.']);continue
            if demo:
                value = dict(id=area['id'] if area else 'demo-folder', title=name, kind='aged',
                    roots=roots, older_than_days=days, recursive=recursive,
                    description='Files last modified more than %d days ago. Demo only.' % days)
            else:
                value = cleanup_area(roots, name, days, recursive, area['id'] if area else None)
            if area:saved.replace([value if a['id'] == area['id'] else a for a in saved.areas])
            else:saved.add(value)
            return value['id']
        if choice == 1:
            value = edit_text(screen, 'Add folder to ' + name, 'Folder path', hint='Paste an absolute path or use ~/. Each matching file is reviewed before deletion.')
            if value:
                path = value if demo else folder_area(value)['roots'][0]
                if path not in roots:roots.append(path)
        elif choice == 2:
            value = edit_text(screen, 'Age filter', 'Days', str(days), 'Match file modification times. Recent files stay.')
            if value:
                try:
                    candidate = int(value)
                    if not 1 <= candidate <= 36500:raise ValueError()
                    days = candidate
                except ValueError:document(screen, 'Invalid age', ['Enter a whole number from 1 to 36500 days.'])
        elif choice == 3:recursive = not recursive
        elif choice == 4:
            value = edit_text(screen, 'Rename group', 'Name', name)
            if value:name = value
        elif choice >= 5:roots.pop(choice - 5)


def add_item(screen, saved, base_areas, demo):
    choice = screen.menu('Add to your list', 'Choose what to track.', ['Cleanup group', 'Known caches and worktrees', 'Size-only folder', 'Cancel'],
                         ['Delete reviewed files older than 10 days in chosen folders.', 'Use an existing cleanup rule.', 'Monitor size without enabling deletion.', 'Return to your list.'])
    if choice == 0:
        return edit_group(screen, saved, demo=demo)
    if choice == 2:
        path = edit_text(screen, 'Add folder', 'Folder path', hint='Paste an absolute path or use ~/. This folder will be size-only.')
        if not path:
            return None
        if demo:
            # Demo entry never touches the user's filesystem.
            area = dict(id='demo-folder', title=Path(path).name or 'Example folder', kind='inspect',
                        roots=['/demo/Added folder'], description='Demo folder. Size only; files stay in place.')
        else:
            area = folder_area(path)
        saved.add(area)
        return area['id']
    if choice == 1:
        known = {a['id'] for a in saved.areas}
        options = demo_areas() if demo else screen.wait('Finding available items', lambda: catalog.expand(base_areas))
        options = [a for a in options if a['id'] not in known and (a['kind'] != 'rust' or a.get('roots'))]
        if not options:
            document(screen, 'Everything is already listed', ['Use Add folder to track another location.'])
            return None
        index = screen.menu('Known caches and worktrees', 'Add one item. Nothing is removed.',
                            [a['title'] for a in options], [a['description'] for a in options])
        if index is not None:
            saved.add(options[index])
            return options[index]['id']
    return None


def old_worktrees(screen, clean):
    import diskpick_tui as tui
    groups = screen.wait('Finding repositories', lambda: tui.worktrees.repositories(tui.discovery.worktrees()))
    groups = {root: [p for p in paths if '.claude/worktrees/' in str(p)] for root, paths in groups.items()}
    roots = sorted(root for root, paths in groups.items() if paths)
    if not roots:
        document(screen, 'No Claude worktrees found', ['No registered .claude/worktrees checkouts were found.'])
        return
    index = screen.menu('Choose a repository', '', [Path(p).name for p in roots], roots)
    if index is not None:
        tui.tree_browser(screen, roots[index], groups[roots[index]], clean)


def draw_list(screen, areas, measurements, index, selected, notice, query, demo, toolbar):
    win = screen.win
    win.erase()
    h, w = win.getmaxyx()
    if h < 15 or w < 58:
        screen.line(0, 'DiskBoard', curses.A_BOLD)
        screen.line(2, 'Enlarge to at least 58 columns × 15 rows.')
        screen.line(h - 2, 'Q  Quit')
        win.refresh()
        return [], []
    free = '18.00 GiB' if demo else ui.amount(shutil.disk_usage(Path.home()).free)
    title = 'DiskBoard  /  Cleanup groups'
    right = free + ' free'
    screen.line(1, title + ' ' * max(2, w - 4 - len(title) - len(right)) + right, curses.A_BOLD)
    actions = [('a', 'Add'), ('c', 'Review'), ('r', 'Refresh' if w >= 70 else 'Scan'), ('o', 'Options' if w >= 70 else 'More')]
    x = 2
    hits = []
    for n, (key, label) in enumerate(actions):
        value = ' ' + label + ' [' + key + '] '
        try:win.addstr(3, x, value, curses.A_REVERSE if toolbar == n else (curses.A_BOLD if n == 0 else 0))
        except curses.error:pass
        hits.append((x, x + len(value), key))
        x += len(value) + 2
    screen.line(4, 'Filter: ' + query + '  ·  Esc clears' if query else 'Storage [1]    Cleanup groups [2]')
    wide = w >= 84
    state_width = 20 if wide else 0
    name_width = w - 4 - 4 - 12 - (state_width + 2 if wide else 0)
    header = '    ' + 'GROUP'.ljust(name_width) + '     ON DISK'
    if wide:header += '  ' + 'CLEANUP'
    screen.line(6, header)
    screen.line(7, '─' * (w - 4))
    step = 2 if h >= 24 else 1
    room = max(1, (h - 13) // step)
    start = max(0, min(index - room // 2, len(areas) - room))
    row_hits = []
    if not areas:
        screen.line(9, 'No matching items.' if query else 'No cleanup groups yet.', curses.A_BOLD)
        screen.line(11, 'Esc clears the filter.' if query else 'Press A to group folders and set an age filter.')
    for n, area in enumerate(areas[start:start + room], start):
        row = measurements.rows.get(area['id'])
        mark = '[x] ' if area['id'] in selected else '[ ] ' if removable(row) else '    '
        size = '…' if row is None else 'Unavailable' if row['total_bytes'] is None else ui.amount(row['total_bytes'])
        label = mark + clip(area['title'], name_width).ljust(name_width) + size.rjust(12)
        if wide:label += '  ' + clip(state_label(row), state_width).ljust(state_width)
        y = 8 + (n - start) * step
        screen.line(y, label, curses.A_REVERSE if n == index and toolbar is None else 0)
        row_hits.append((y, n))
    screen.line(h - 5, '─' * (w - 4))
    if areas:
        area = areas[index]
        row = measurements.rows.get(area['id'])
        path = next(iter(area.get('roots', [])), area['description'])
        context = (area['description'] if area['kind'] == 'aged' else state_label(row) + '  ·  ' + short_path(path))
        screen.line(h - 4, clip(context, w - 4))
    screen.line(h - 3, clip(notice or ('↑ ↓ Move   Enter Open   Space Select   / Filter   Tab Tools' if w >= 70 else '↑ ↓ Move  Enter Open  Space Select  Tab Tools'), w - 4))
    checking = ' · checking %d' % len(measurements.pending) if measurements.pending else ''
    footer = '%d items%s' % (len(areas), checking)
    if demo:footer += ' · DEMO DATA'
    if len(areas) > room:footer += ' · %d–%d shown' % (start + 1, min(start + room, len(areas)))
    screen.line(h - 2, footer + ' ' * max(2, w - 4 - len(footer) - 6) + 'Q Quit')
    win.refresh()
    return hits, row_hits


def run(screen, areas, scan, clean, demo=False, offer_agent=False, config=None):
    jobs = []
    def measurements(items):
        task = Measurements(items, demo_scan if demo else scan, isolate=not demo)
        jobs.append(task)
        return task
    try:
        return run_list(screen, areas, scan, clean, demo, offer_agent, config, measurements)
    finally:
        for task in jobs:
            task.close()


def run_list(screen, areas, scan, clean, demo, offer_agent, config, measurements):
    base = demo_areas() if demo else areas
    saved = SavedList(base, config, demo)
    measure = measurements(saved.areas)
    index = 0
    selected = set()
    notice = ''
    query = ''
    toolbar = None
    undo = None
    while True:
        measure.poll()
        selected.intersection_update(a['id'] for a in saved.areas if removable(measure.rows.get(a['id'])))
        visible = [a for a in saved.areas if query.casefold() in (a['title'] + ' ' + ' '.join(a.get('roots', []))).casefold()]
        index = max(0, min(index, len(visible) - 1))
        toolbar = min(toolbar, 3) if toolbar is not None else None
        hits, row_hits = draw_list(screen, visible, measure, index, selected, notice, query, demo, toolbar)
        screen.win.timeout(150)
        key = screen.win.getch()
        if ord('A') <= key <= ord('Z'):key += 32
        if key == -1:
            continue
        screen.win.timeout(-1)
        if key in (ord('q'), ord('Q')):
            return 0
        if key == ord('1'):
            return 'storage'
        if screen.win.getmaxyx()[0] < 15 or screen.win.getmaxyx()[1] < 58:
            continue
        if key == curses.KEY_RESIZE:
            continue
        if key == 27:
            query = ''; toolbar = None; notice = ''
            continue
        if key == 9:
            toolbar = 0 if toolbar is None else toolbar + 1
            if toolbar >= len(hits):toolbar = None
            continue
        if toolbar is not None and key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            toolbar = (toolbar + (1 if key == curses.KEY_RIGHT else -1)) % len(hits)
            continue
        if toolbar is not None and key in (10, 13, curses.KEY_ENTER):
            key = ord(hits[toolbar][2]); toolbar = None
        if key == curses.KEY_MOUSE:
            try:
                _, x, y, _, state = curses.getmouse()
                if state & getattr(curses, 'BUTTON4_PRESSED', 0):index = max(0, index - 3)
                elif state & getattr(curses, 'BUTTON5_PRESSED', 0):index = min(len(visible) - 1, index + 3)
                elif state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_DOUBLE_CLICKED):
                    if y == 3:
                        key = next((ord(k) for left, right, k in hits if left <= x < right), -1)
                    else:
                        index = next((n for row_y, n in row_hits if row_y == y), index)
                        toolbar = None
                        if any(row_y == y for row_y, _ in row_hits):
                            key = ord(' ') if x < 6 else 10 if state & curses.BUTTON1_DOUBLE_CLICKED else -1
            except curses.error:
                pass
        if key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
            delta = {curses.KEY_DOWN: 1, curses.KEY_UP: -1, curses.KEY_NPAGE: max(1, len(row_hits)), curses.KEY_PPAGE: -max(1, len(row_hits))}.get(key, 0)
            index = 0 if key == curses.KEY_HOME else len(visible) - 1 if key == curses.KEY_END else max(0, min(len(visible) - 1, index + delta))
            toolbar = None; notice = ''
            continue
        area = visible[index] if visible else None
        row = measure.rows.get(area['id']) if area else None
        try:
            if key == ord('/'):
                query = edit_text(screen, 'Filter your list', 'Name or path', query, 'Leave blank to show every item.') or ''
                index = 0
            elif key in (10, 13, curses.KEY_ENTER) and area:
                choice = screen.menu(area['title'], area['description'], ['Review files to delete', 'Edit folders and age filter', 'View details', 'Back'])
                if choice == 0:
                    if review_selected(screen, [area], scan, clean, demo):
                        measure = measurements(saved.areas);selected.clear()
                elif choice == 1:
                    if edit_group(screen, saved, area, demo=demo):
                        measure = measurements(saved.areas);selected.clear();notice = 'Group saved.'
                elif choice == 2:details(screen, area, row)
            elif key == ord(' ') and area:
                if removable(row):
                    if area['id'] in selected:selected.remove(area['id'])
                    else:selected.add(area['id'])
                    notice = ''
                else:
                    notice = 'Still checking this item.' if row is None else 'No cleanup available. Enter shows details.'
            elif key == ord('a'):
                added = add_item(screen, saved, base, demo)
                if added:
                    # Keep measured rows; only the new entry needs an inspection.
                    new_measure = measurements([a for a in saved.areas if a['id'] == added])
                    # Keep receiving the earlier inventory while this new item is measured.
                    measure.poll()
                    previous = measure
                    measure = CombinedMeasurements(previous, new_measure)
                    query = ''; index = next(i for i, a in enumerate(saved.areas) if a['id'] == added)
                    notice = 'Added to your list.'
            elif key == ord('r'):
                if measure.pending:
                    notice = 'Measurements are still running. You can keep using the list.'
                else:
                    measure = measurements(saved.areas)
                    selected.clear(); notice = ''
            elif key in (ord('c'), ord('d')):
                chosen = [a for a in saved.areas if a['id'] in selected] if selected else ([area] if area else [])
                if not chosen:
                    notice = 'Add a cleanup group first.';continue
                if review_selected(screen, chosen, scan, clean, demo):
                    measure = measurements(saved.areas)
                    selected.clear()
            elif key == ord('u') and undo is not None:
                saved.restore(*undo); undo = None
                measure = measurements(saved.areas)
                notice = 'Item restored.'
            elif key == ord('o'):
                choices = (['Rename item', 'Remove from list'] if area else [])
                if undo is not None:choices.append('Undo removal')
                choices += ['Review Claude worktrees']
                if offer_agent:choices.append('Open agent report')
                choices += ['Help', 'Back']
                picked = screen.menu('Options', area['title'] if area else 'Your list', choices)
                action = choices[picked] if picked is not None else 'Back'
                if action == 'Rename item':
                    name = edit_text(screen, 'Rename item', 'Name', area['title'])
                    if name:
                        saved.replace([dict(a, title=name) if a['id'] == area['id'] else a for a in saved.areas])
                        notice = 'Name saved.'
                elif action == 'Remove from list':
                    before = saved.areas
                    saved.replace([a for a in saved.areas if a['id'] != area['id']])
                    undo = (area, next(i for i, a in enumerate(before) if a['id'] == area['id']))
                    selected.discard(area['id'])
                    notice = 'Removed from list. Files kept. U to undo.'
                elif action == 'Undo removal':
                    saved.restore(*undo); undo = None
                    measure = measurements(saved.areas)
                    notice = 'Item restored.'
                elif action == 'Open agent report':
                    if demo:document(screen, 'Agent report', ['The live app opens your Claude or Codex CLI. The demo makes no AI request.'])
                    else:return 'agent'
                elif action == 'Review Claude worktrees':
                    if demo:document(screen, 'Worktree review', ['The live app lets you choose a repository and review inactive Claude worktrees.'])
                    else:old_worktrees(screen, clean)
                elif action == 'Help':
                    document(screen, 'Your list', ['A adds a folder or known cache. R refreshes sizes.',
                             'Enter opens details. Space selects an eligible item. C reviews the exact paths before removal.',
                             '/ filters by name or path. Tab focuses toolbar actions. Q quits.', '',
                             'Cleanup groups store folders and an age rule. Enter opens the review and editor. Remove from list only removes the row.',
                             'Cleanup rules recheck paths before removal. Active work and uncertain files stay.', '',
                             'List settings: ' + ('Not saved in demo mode.' if demo else str(saved.path))])
        except (OSError, ValueError, engine.Unsafe) as exc:
            document(screen, 'Could not finish', [str(exc), '', 'Your existing settings and unreviewed files are kept.'])


class CombinedMeasurements:
    """Merge an added row while preserving an in-flight inventory."""
    def __init__(self, old, new):
        self.old, self.new = old, new
        self.rows = {}
        self.pending = set()
        self.poll()

    def poll(self):
        self.old.poll(); self.new.poll()
        self.rows = dict(self.old.rows, **self.new.rows)
        self.pending = self.old.pending | self.new.pending


if __name__ == '__main__':
    # Private read-only measurement worker. No cleanup command is exposed here.
    from concurrent.futures import ThreadPoolExecutor, as_completed
    input_areas = catalog.validate(dict(version=1, areas=json.load(sys.stdin)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(catalog.scan, area): area for area in input_areas}
        for future in as_completed(futures):
            area = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = dict(area=area, total_bytes=None, eligible_bytes=0, preview=[], paths=[],
                              reasons=[str(exc)], status='SKIPPED')
            print(json.dumps(result), flush=True)
