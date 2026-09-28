"""Responsive terminal storage tree with bounded scan effort and saved groups."""
import curses
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

import diskpick_catalog as catalog
import diskpick_engine as engine
from diskpick_list import SavedList, folder_area
from diskpick_list_ui import clip, document, edit_text, short_path
from diskpick_setup import settings_directory
from diskpick_tree import EFFORTS
import diskpick_ui as ui

CACHE_LIMIT = 2 * 1024 * 1024


def default_root():
    # macOS keeps writable user/app data on the Data volume. Starting here avoids
    # walking the sealed system volume and its mirrored firmlink paths twice.
    if sys.platform == 'darwin' and Path('/System/Volumes/Data').is_dir():
        return Path('/System/Volumes/Data')
    return Path.home()


def cache_path(root, config=None):
    parent = Path(config).absolute().parent if config else catalog.CONFIG.parent
    key = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    return parent / ('.overview-' + key + '.json')


def read_cache(root, config=None):
    path = cache_path(root, config)
    try:
        with engine.directory(path.parent) as fd:
            f = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            with os.fdopen(f, 'rb') as source:
                st = os.fstat(source.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_nlink != 1 or st.st_mode & 0o022 or st.st_size > CACHE_LIMIT:
                    return None
                data = json.loads(source.read(CACHE_LIMIT + 1))
        if not isinstance(data, dict):return None
        if data.get('version') != 1 or data.get('root') != str(root) or not isinstance(data.get('rows'), list):return None
        if len(data['rows']) > 1200:return None
        stamp = data.get('captured_at')
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= stamp <= time.time() + 86400:return None
        if type(data.get('complete')) is not bool or not isinstance(data.get('reason', ''), str):return None
        if not all(type(data.get(k)) in (int, float) and math.isfinite(data[k]) and 0 <= data[k] <= 2**70 for k in ('bytes', 'entries', 'issues', 'depth', 'elapsed')):return None
        if type(data['depth']) is not int or not 1 <= data['depth'] <= 6:return None
        for row in data['rows']:
            if not isinstance(row, dict) or row.get('kind') not in ('folder', 'file', 'other'):return None
            p = Path(row['path'])
            if p.is_absolute() or '..' in p.parts or not isinstance(row['name'], str):return None
            if type(row['bytes']) is not int or not 0 <= row['bytes'] <= 2**70:return None
            if type(row.get('complete')) is not bool or type(row['level']) is not int or not 1 <= row['level'] <= 6:return None
        focus = Path(data.get('focus', ''))
        if focus.is_absolute() or '..' in focus.parts:return None
        data['cached'] = True
        data['running'] = False
        return data
    except (OSError, ValueError, TypeError, KeyError, RecursionError, engine.Unsafe):
        return None


def write_cache(snapshot, config=None):
    if not snapshot or snapshot.get('cached'):return
    path = cache_path(snapshot['root'], config)
    data = (json.dumps(snapshot) + '\n').encode()
    if len(data) > CACHE_LIMIT:return
    try:
        with settings_directory(path.parent) as fd:
            try:
                existing = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(existing.st_mode) or existing.st_nlink != 1 or existing.st_uid != os.getuid() or existing.st_mode & 0o022:return
            except FileNotFoundError:pass
            name = '.overview-' + uuid.uuid4().hex + '.tmp'
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                with os.fdopen(out, 'wb') as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
                os.rename(name, path.name, src_dir_fd=fd, dst_dir_fd=fd)
            finally:
                try:os.unlink(name, dir_fd=fd)
                except FileNotFoundError:pass
    except (OSError, ValueError, engine.Unsafe):
        # The cache is disposable; a cache-write failure never blocks inspection.
        pass


class Overview:
    def __init__(self, root, config=None):
        self.root = Path(root)
        self.config = config
        self.snapshot = read_cache(root, config)
        self.error = ''
        self.closing = False
        self.process = subprocess.Popen([sys.executable, '-B', str(Path(__file__).with_name('diskpick_tree.py'))],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        text=True, start_new_session=True)
        self.send(action='start', root=str(root))
        def receive():
            try:
                for line in self.process.stdout:
                    self.snapshot = json.loads(line)
                if self.process.wait() != 0 and not self.closing:
                    self.error = 'The scan stopped. Choose a readable folder or refresh.'
            except (ValueError, OSError):
                self.error = 'The scan stopped. Refresh to try again.'
            finally:
                self.process.stdout.close()
        self.thread = threading.Thread(target=receive, daemon=True)
        self.thread.start()

    def send(self, **command):
        try:
            self.process.stdin.write(json.dumps(command) + '\n')
            self.process.stdin.flush()
        except (OSError, ValueError):
            self.error = 'The scan stopped. Refresh to try again.'

    def close(self):
        self.closing = True
        write_cache(self.snapshot, self.config)
        if self.process.poll() is None:
            try:os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:pass
            try:self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:pass
                self.process.wait(timeout=1)
        self.thread.join(timeout=.5)
        self.process.stdin.close()
        self.process.stderr.close()


def demo_snapshot(focus='', depth=2):
    gib = engine.GIB
    values = [
        ('Users', 226, False), ('Users/you', 226, False),
        ('Users/you/Projects', 94, False), ('Users/you/Projects/Studio', 51, True),
        ('Users/you/Projects/Studio/target', 38, True),
        ('Users/you/Library', 82, False), ('Users/you/Library/Caches', 26, True),
        ('Users/you/Movies', 34, True), ('Users/you/Downloads', 16, True),
        ('Applications', 38, True), ('Applications/Creative tools', 24, True),
        ('Applications/Developer tools', 14, True), ('Library', 18, False),
        ('Library/Developer', 15, False), ('Library/Updates', 3, True), ('private', 12, False)]
    current = next((v for p, v, _ in values if p == focus), 294)
    rows = []
    for path, size, complete in values:
        relative = path[len(focus) + 1:] if focus else path
        if focus and not path.startswith(focus + '/'):continue
        level = relative.count('/') + 1
        if level > depth:continue
        rows.append(dict(path=path, name=Path(path).name, kind='folder', level=level,
                         bytes=int(size * gib), complete=complete, reason='', issues=0, children=1))
    return dict(version=1, root='/demo/Startup disk', focus=focus, rows=rows, depth=depth,
                bytes=int(current * gib), complete=False, finished=False, running=False, effort=0,
                entries=20000, folders=2431, elapsed=2, issues=0, skipped_links=12, omitted=0,
                captured_at=time.time(), capped=False)


def draw(screen, snapshot, index, notice, demo, depth, effort):
    win = screen.win
    win.erase()
    h, w = win.getmaxyx()
    title = 'DiskBoard  /  Storage'
    try:
        usage = shutil.disk_usage(snapshot['root'] if snapshot and not demo else Path.home())
        used, free = (430 * engine.GIB, 18 * engine.GIB) if demo else (usage.used, usage.free)
        right = (ui.amount(used) + ' used · ' if w >= 90 else '') + ui.amount(free) + ' free'
        title += ' ' * max(2, w - 4 - len(title) - len(right)) + right
    except OSError:pass
    compact = h < 26
    screen.line(0 if compact else 1, title, curses.A_BOLD)
    screen.line(2 if compact else 3, 'Storage [1]    Cleanup groups [2]    Options [o]')
    if h < 20 or w < 60:
        screen.line(5, 'Enlarge to 60 columns × 20 rows. Q quits.')
        win.refresh(); return []
    if not snapshot:
        screen.line(6, notice or 'Opening a quick, read-only scan…')
        screen.line(h - 2, 'O  Choose folder   R  Retry   2  Groups   Q  Quit')
        win.refresh(); return []
    scope = Path(snapshot['root']) / snapshot.get('focus', '')
    screen.line(3 if compact else 5, clip(short_path(scope), w - 4), curses.A_BOLD)
    depth_control = 'Visible levels: %d [- +]' % depth
    effort_control = 'Scan effort: ' + EFFORTS[effort][0] + ' [e]'
    screen.line(4 if compact else 6, depth_control + '    ' + effort_control)
    state = 'Scanning' if snapshot.get('running') else 'Measured' if snapshot['complete'] else 'Paused — M continues'
    if snapshot.get('cached'):state = 'Last scan ' + time.strftime('%H:%M', time.localtime(snapshot['captured_at']))
    screen.line(5 if compact else 7, '%s · %s found · %s entries' % (state, ui.amount(snapshot['bytes']), format(snapshot['entries'], ',')))
    wide = w >= 90
    size_width = 19
    bar_width = 12 if wide else 0
    right_width = size_width + (bar_width + 2 if wide else 0) + 10
    name_width = w - 4 - right_width
    header_y = 7 if compact else 9
    first_y = header_y + 2
    footer_y = h - (5 if compact else 6)
    screen.line(header_y, 'FOLDER / FILE'.ljust(name_width) + 'FOUND ON DISK'.rjust(size_width) + ('  ' + 'SHARE OF FOUND'.ljust(bar_width + 10) if wide else '   % FOUND'))
    screen.line(header_y + 1, '─' * (w - 4))
    rows = snapshot['rows']
    room = max(1, footer_y - first_y)
    start = max(0, min(index - room // 2, len(rows) - room))
    hits = []
    for n, row in enumerate(rows[start:start + room], start):
        prefix = '  ' * (row['level'] - 1) + ('▸ ' if row['kind'] == 'folder' else '· ')
        name = clip(prefix + row['name'], name_width)
        size = ui.amount(row['bytes']) + ('' if row['complete'] else ' so far')
        if not row['complete'] and row['bytes'] == 0:size = 'Not measured'
        share = row['bytes'] / snapshot['bytes'] if snapshot['bytes'] else 0
        bar = ('  ' + ('━' * round(share * bar_width)).ljust(bar_width, '·')) if wide else ''
        value = name.ljust(name_width) + size.rjust(size_width) + bar + ('%7.1f%%' % (share * 100))
        y = first_y + n - start
        screen.line(y, value, curses.A_REVERSE if n == index else 0)
        hits.append((y, n))
    if not rows:screen.line(first_y, 'No entries found yet.' if snapshot.get('running') else 'No readable entries in this folder.')
    screen.line(footer_y, '─' * (w - 4))
    if rows:
        screen.line(footer_y + 1, clip(str(Path(snapshot['root']) / rows[index]['path']), w - 4))
    description = snapshot.get('reason') or ('Partial sizes grow as scanning continues. % of found bytes.')
    if snapshot.get('issues'):description += ' %d checks incomplete.' % snapshot['issues']
    if snapshot.get('capped'):description = 'Folder limit reached. Options → Choose a folder to scan further.'
    if snapshot.get('omitted'):description = 'Some rows omitted. Zoom into a folder to see more.'
    screen.line(footer_y + 2, clip(notice or description, w - 4))
    screen.line(footer_y + 3, 'M Continue  P Pause  T Add to group  F Scan folder  Q Quit')
    if not compact:
        screen.line(h - 2, 'Enter Open  ← Back  ↑↓ Move · ' + ('DEMO DATA · ' if demo else '') + '%d–%d of %d rows · %.1fs scanning' % (min(start+1,len(rows)), min(start+room,len(rows)), len(rows), snapshot['elapsed']))
    if compact:screen.line(h - 1, 'Enter Open  ← Back  ↑↓ Move')
    win.refresh()
    return hits


def storage(screen, areas, demo=False, config=None, root=None):
    root = Path(root) if root else default_root()
    session = None if demo else Overview(root, config)
    depth = 2; effort = 0; focus = ''; index = 0; selected = None; notice = ''
    snapshot = demo_snapshot() if demo else session.snapshot
    try:
        while True:
            previous = snapshot
            snapshot = demo_snapshot(focus, depth) if demo else session.snapshot
            if snapshot is not None and snapshot is not previous and selected:
                index = next((i for i, r in enumerate(snapshot['rows']) if (r['kind'], r['path']) == selected), 0)
            if snapshot:
                index = max(0, min(index, len(snapshot['rows']) - 1))
            hits = draw(screen, snapshot, index, notice or (session.error if session else ''), demo, depth, effort)
            screen.win.timeout(100)
            key = screen.win.getch()
            if ord('A') <= key <= ord('Z'):key += 32
            if key == -1:continue
            screen.win.timeout(-1)
            if key in (ord('q'), 27):return 0
            if key == ord('2'):return 'groups'
            if key == curses.KEY_RESIZE:continue
            if screen.win.getmaxyx()[0] < 20 or screen.win.getmaxyx()[1] < 60:continue
            if key == curses.KEY_MOUSE:
                try:
                    _, x, y, _, state = curses.getmouse()
                    if state & getattr(curses, 'BUTTON4_PRESSED', 0):key = curses.KEY_UP
                    elif state & getattr(curses, 'BUTTON5_PRESSED', 0):key = curses.KEY_DOWN
                    elif state & (curses.BUTTON1_CLICKED | curses.BUTTON1_PRESSED | curses.BUTTON1_DOUBLE_CLICKED):
                        if y == (2 if screen.win.getmaxyx()[0] < 26 else 3):
                            if 17 <= x < 35:return 'groups'
                            if x >= 35:key = ord('o')
                        elif y == (4 if screen.win.getmaxyx()[0] < 26 else 6):
                            if 21 <= x < 23:key = ord('-')
                            elif 23 <= x < 26:key = ord('+')
                            elif x >= 29:key = ord('e')
                        elif y == screen.win.getmaxyx()[0] - (2 if screen.win.getmaxyx()[0] < 26 else 3):
                            key = ord('m' if x < 14 else 'p' if x < 23 else 't' if x < 39 else 'f' if x < 53 else 'q')
                        else:
                            index = next((n for row_y, n in hits if row_y == y), index)
                            if snapshot and snapshot['rows']:
                                selected = (snapshot['rows'][index]['kind'], snapshot['rows'][index]['path'])
                            if state & curses.BUTTON1_DOUBLE_CLICKED:key = 10
                except curses.error:pass
            if not snapshot and key not in (ord('o'), ord('r')):
                continue
            rows = snapshot['rows'] if snapshot else []
            row = rows[index] if rows else None
            if key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_NPAGE, curses.KEY_PPAGE, curses.KEY_HOME, curses.KEY_END):
                delta = {curses.KEY_DOWN:1, curses.KEY_UP:-1, curses.KEY_NPAGE:max(1,len(hits)), curses.KEY_PPAGE:-max(1,len(hits))}.get(key, 0)
                index = 0 if key == curses.KEY_HOME else len(rows)-1 if key == curses.KEY_END else max(0,min(len(rows)-1,index+delta))
                if rows:selected = (rows[index]['kind'], rows[index]['path'])
                notice = ''; continue
            try:
                if key in (ord('+'), ord('='), ord('-')):
                    depth = max(1, min(6, depth + (-1 if key == ord('-') else 1)))
                    if session:session.send(action='view', focus=focus, depth=depth)
                    notice = 'Showing %d levels. This changes the view; M scans more data.' % depth
                elif key in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT) and row:
                    if row['kind'] == 'folder':
                        focus = row['path']; index = 0; selected = None
                        if session:session.send(action='view', focus=focus, depth=depth)
                    else:
                        document(screen, row['name'], [str(Path(snapshot['root']) / row['path']), '',
                            'Allocated on disk: ' + ui.amount(row['bytes']),
                            'Read-only inventory. Add a containing folder to your groups to track it.'])
                elif key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, 127):
                    focus = str(Path(snapshot.get('focus', '')).parent)
                    if focus == '.':focus = ''
                    index = 0; selected = None
                    if session:session.send(action='view', focus=focus, depth=depth)
                elif key == ord('e'):
                    labels = ['Quick · up to 2 seconds / 20,000 entries', 'More · up to 5 seconds / 60,000 entries',
                              'Detailed · up to 15 seconds / 200,000 entries', 'Deep · up to 1 minute / 800,000 entries',
                              'Full · no time limit; up to 50,000 folders; P pauses']
                    choice = screen.menu('Scan effort', 'More effort inspects more metadata. File contents are never read.', labels)
                    if choice is not None:
                        effort = choice
                        if session:session.send(action='effort', effort=effort)
                        notice = 'Additional ' + EFFORTS[effort][0].lower() + ' scan requested.'
                elif key == ord('m'):
                    if session:session.send(action='more', effort=max(1, effort))
                    notice = '' if session else 'Demo only. The live scan continues from its previous position.'
                elif key == ord('p'):
                    if session:session.send(action='pause')
                    notice = 'Paused. M continues the scan.'
                elif key == ord('t'):
                    if not row or row['kind'] != 'folder':
                        notice = 'Select a folder first. Enter opens folders; arrows choose a row.'
                    else:
                        from diskpick_list_ui import edit_group
                        saved = SavedList(areas, config, demo)
                        path = str(Path(snapshot['root']) / row['path'])
                        existing = next((a for a in saved.areas if path in a.get('roots', [])), None)
                        if edit_group(screen, saved, existing, initial_root=path, demo=demo):
                            return 'groups'
                        notice = 'Cancelled. No group was changed.'
                elif key == ord('f'):
                    if not row or row['kind'] != 'folder':
                        notice = 'Select a folder to scan.'
                    elif not demo:
                        target = Path(snapshot['root']) / row['path']
                        with engine.directory(target):pass
                        session.close(); session = Overview(target, config)
                        root = target; focus = ''; index = 0; selected = None
                        notice = 'Scanning this folder on its own.'
                elif key in (ord('o'), ord('r')):
                    choice = 3 if key == ord('r') else screen.menu('Storage options', '',
                        ['Scan Home', 'Scan startup disk data', 'Choose a folder', 'Start a fresh scan', 'How sizes work', 'Back'])
                    if choice in (0, 1, 2, 3):
                        target = Path.home() if choice == 0 else default_root() if choice == 1 else root
                        if choice == 2:
                            value = edit_text(screen, 'Choose a scan folder', 'Folder path', hint='Read-only scan. Use an absolute path or ~/.')
                            if not value:continue
                            target = Path(folder_area(value)['roots'][0]) if not demo else Path('/demo/Startup disk')
                        if not demo:
                            with engine.directory(target):pass
                            session.close(); session = Overview(target, config)
                        root = target; focus = ''; index = 0; selected = None; notice = ''; depth = 2; effort = 0
                    elif choice == 4:
                        document(screen, 'How sizes work', [
                            'Quick scans stop after 2 seconds of scanning or 20,000 entries. More continues where the scan paused.',
                            'Depth only changes how many levels are drawn. Hidden descendants still contribute to measured folder totals.',
                            'An unfinished folder says so far: its measured size can still grow. A large unseen file can change the ranking.',
                            'Percentages are shares of the data found in the current folder. They are not scan completion or percentages of the entire disk.',
                            'Sizes use allocated blocks. Sparse files use less than their logical size. Hard links count once per scan; APFS clones may share blocks, so folder totals need not match physical disk usage.',
                            'Symlinks are not followed, other filesystems are not crossed, and unreadable folders stay marked incomplete.',
                            'Only directory totals and each folder’s five largest files are retained. Other files stay aggregated. A 50,000-folder limit bounds memory; zoom by choosing a smaller scan root if reached.',
                            'The latest visible overview is saved locally and timestamped. It is replaced by a fresh quick scan on reopening. No file contents are stored.'])
            except (OSError, ValueError, engine.Unsafe) as exc:
                document(screen, 'Could not finish', [str(exc)])
    finally:
        screen.win.timeout(-1)
        if session:session.close()


def run_app(screen, areas, scan, clean, demo=False, offer_agent=False, config=None, start='storage', storage_root=None):
    from diskpick_list_ui import run as groups
    page = start
    while True:
        if page == 'storage':result = storage(screen, areas, demo, config, storage_root)
        else:result = groups(screen, areas, scan, clean, demo, offer_agent, config)
        if result == 'groups':page = 'groups'
        elif result == 'storage':page = 'storage'
        else:return result
