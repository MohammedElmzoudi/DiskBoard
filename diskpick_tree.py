"""Bounded, read-only storage inventory. Never read file contents or follow links.

Depth controls presentation, not an expensive recursive du. Scanning advances in
small round-robin batches; unfinished sizes are lower bounds, never estimates of
unvisited data. Only directory records and a few largest files are retained.
"""
from collections import deque
from dataclasses import dataclass, field
import heapq
import os
from pathlib import Path
import stat
import time

import diskpick_engine as engine

MAX_FOLDERS = 50000
MAX_OPEN = 24
LARGEST_FILES = 5
MAX_ROWS = 1200


@dataclass
class Folder:
    path: str
    parent: str = None
    bytes: int = 0
    direct_bytes: int = 0
    files: int = 0
    children: list = field(default_factory=list)
    largest: list = field(default_factory=list)
    waiting: int = 0
    listed: bool = False
    done: bool = False
    issues: int = 0
    reason: str = ''


class Scanner:
    def __init__(self, root, max_folders=MAX_FOLDERS):
        self.root = Path(root).expanduser()
        with engine.directory(self.root) as fd:
            self.device = os.fstat(fd).st_dev
        self.nodes = {'': Folder('', None)}
        self.pending = deque([''])
        self.active = deque()
        self.links = set()
        self.entries = 0
        self.elapsed = 0.0
        self.max_folders = max_folders
        self.skipped_links = 0
        self.capped = False

    @property
    def finished(self):
        return not self.pending and not self.active

    def add_bytes(self, key, amount):
        node = self.nodes[key]
        node.direct_bytes += amount
        while node is not None:
            node.bytes += amount
            node = self.nodes.get(node.parent)

    def issue(self, key, reason):
        node = self.nodes[key]
        node.reason = reason
        while node is not None:
            node.issues += 1
            node = self.nodes.get(node.parent)

    def finish(self, key):
        node = self.nodes[key]
        while node.listed and node.waiting == 0 and not node.done:
            node.done = True
            if node.parent is None:
                break
            node = self.nodes[node.parent]
            node.waiting -= 1

    def open_pending(self):
        attempts = 0
        while self.pending and len(self.active) < MAX_OPEN and attempts < 32:
            attempts += 1
            key = self.pending.popleft()
            node = self.nodes[key]
            context = engine.directory(self.root / key)
            entered = False
            try:
                fd = context.__enter__(); entered = True
                st = os.fstat(fd)
                if st.st_dev != self.device:
                    raise engine.Unsafe('Another filesystem; open this folder as a separate scan.')
                iterator = os.scandir(fd)
                self.add_bytes(key, st.st_blocks * 512)
                self.active.append((key, iterator, context, fd, st.st_mtime_ns))
            except (OSError, engine.Unsafe) as exc:
                if entered:context.__exit__(None, None, None)
                self.issue(key, str(exc))
                node.listed = True
                self.finish(key)

    def entry(self, key, entry):
        self.entries += 1
        node = self.nodes[key]
        try:
            if entry.is_symlink():
                self.skipped_links += 1
                return
            if entry.is_dir(follow_symlinks=False):
                if len(self.nodes) >= self.max_folders or key.count('/') >= 63:
                    self.capped = True
                    self.issue(key, 'Folder limit reached. Open a smaller folder to continue.')
                    return
                relative = str(Path(key) / entry.name)
                self.nodes[relative] = Folder(relative, key)
                node.children.append(relative)
                node.waiting += 1
                self.pending.append(relative)
                return
            st = entry.stat(follow_symlinks=False)
            if st.st_dev != self.device:
                self.issue(key, 'An entry is on another filesystem.'); return
            if not stat.S_ISREG(st.st_mode):
                return
            # Count hard-linked allocation once per scan, as du does by default.
            if st.st_nlink > 1:
                identity = (st.st_dev, st.st_ino)
                if identity in self.links:return
                if len(self.links) >= 100000:
                    self.issue(key, 'Hard-link tracking limit reached.'); return
                self.links.add(identity)
            allocated = st.st_blocks * 512
            self.add_bytes(key, allocated)
            node.files += 1
            item = (allocated, entry.name)
            if len(node.largest) < LARGEST_FILES:heapq.heappush(node.largest, item)
            elif item > node.largest[0]:heapq.heapreplace(node.largest, item)
        except OSError as exc:
            self.issue(key, str(exc))

    def advance(self, max_entries=512, seconds=.04):
        started = time.monotonic()
        limit = self.entries + max_entries
        try:
            while not self.finished and self.entries < limit and time.monotonic() - started < seconds:
                self.open_pending()
                if not self.active:continue
                key, iterator, context, fd, original_mtime = self.active.popleft()
                exhausted = False
                try:
                    # A huge directory cannot monopolize a quick scan.
                    for _ in range(min(32, limit - self.entries)):
                        if time.monotonic() - started >= seconds:break
                        self.entry(key, next(iterator))
                except StopIteration:
                    exhausted = True
                except OSError as exc:
                    self.issue(key, str(exc)); exhausted = True
                if exhausted:
                    try:
                        if os.fstat(fd).st_mtime_ns != original_mtime:
                            self.issue(key, 'Folder changed during the scan. Refresh for newer sizes.')
                    except OSError as exc:self.issue(key, str(exc))
                    iterator.close(); context.__exit__(None, None, None)
                    self.nodes[key].listed = True
                    self.finish(key)
                else:
                    self.active.append((key, iterator, context, fd, original_mtime))
        finally:
            self.elapsed += time.monotonic() - started

    def prioritize(self, key):
        if key not in self.nodes:return
        def inside(value):return value == key or value.startswith(key + '/')
        self.pending = deque(sorted(self.pending, key=lambda p: not inside(p)))
        self.active = deque(sorted(self.active, key=lambda item: not inside(item[0])))

    def snapshot(self, focus='', depth=2, max_rows=MAX_ROWS):
        focus = focus if focus in self.nodes else ''
        root = self.nodes[focus]
        rows = []
        omitted = 0
        def visit(key, level):
            nonlocal omitted
            node = self.nodes[key]
            values = [(self.nodes[child].bytes, child, 'folder') for child in node.children]
            # Retain only the five largest files; the rest stay aggregated.
            values += [(size, str(Path(key) / name), 'file') for size, name in node.largest]
            values.sort(key=lambda item: (-item[0], item[1]))
            for size, path, kind in values:
                if len(rows) >= max_rows:
                    omitted += 1; continue
                child = self.nodes[path] if kind == 'folder' else None
                rows.append(dict(path=path, name=Path(path).name, kind=kind, level=level, bytes=size,
                    complete=child.done and child.issues == 0 if child else True,
                    reason=child.reason if child else '', issues=child.issues if child else 0,
                    children=len(child.children) if child else 0))
                if child and level < depth:
                    visit(path, level + 1)
            extra = node.direct_bytes - sum(size for size, name in node.largest)
            if extra > 0 and len(rows) < max_rows:
                rows.append(dict(path=key, name='Other files & folder metadata', kind='other', level=level,
                                 bytes=extra, complete=node.listed, reason='', issues=0, children=0))
        visit(focus, 1)
        return dict(version=1, root=str(self.root), focus=focus, depth=depth, rows=rows,
                    bytes=root.bytes, complete=root.done and root.issues == 0,
                    finished=self.finished, issues=root.issues, reason=root.reason, entries=self.entries,
                    folders=len(self.nodes), elapsed=round(self.elapsed, 3), capped=self.capped,
                    skipped_links=self.skipped_links, omitted=omitted, captured_at=time.time())

    def close(self):
        while self.active:
            _, iterator, context, _, _ = self.active.popleft()
            iterator.close(); context.__exit__(None, None, None)
        self.pending.clear()


# Effort is a time / metadata budget, never a fabricated percent of the disk.
EFFORTS = [('Quick', 2, 20000), ('More', 5, 60000), ('Detailed', 15, 200000),
           ('Deep', 60, 800000), ('Full', None, None)]


def worker():
    import json
    import queue
    import sys
    import threading
    commands = queue.Queue()
    def read_commands():
        for line in sys.stdin:
            try:commands.put(json.loads(line))
            except ValueError:pass
        commands.put({'action': 'quit'})
    threading.Thread(target=read_commands, daemon=True).start()
    first = commands.get()
    scanner = Scanner(first['root'])
    focus = ''; depth = 2; effort = 0; running = True
    seconds_limit = EFFORTS[0][1]; entries_limit = EFFORTS[0][2]
    last = 0
    dirty = True
    try:
        while True:
            while not commands.empty():
                command = commands.get_nowait()
                action = command.get('action')
                if action == 'quit':return
                if action in ('more', 'effort'):
                    effort = max(0, min(4, int(command.get('effort', effort))))
                    seconds, entries = EFFORTS[effort][1:]
                    seconds_limit = scanner.elapsed + seconds if seconds is not None else None
                    entries_limit = scanner.entries + entries if entries is not None else None
                    running = True
                elif action == 'pause':running = False
                elif action == 'view':
                    focus = command.get('focus', focus)
                    depth = max(1, min(6, int(command.get('depth', depth))))
                    scanner.prioritize(focus)
                dirty = True
            if running and not scanner.finished:
                scanner.advance(max_entries=min(512, max(0, entries_limit - scanner.entries)) if entries_limit else 512)
                if ((seconds_limit is not None and scanner.elapsed >= seconds_limit) or
                    (entries_limit is not None and scanner.entries >= entries_limit)):
                    running = False; dirty = True
            if scanner.finished and running:running = False; dirty = True
            now = time.monotonic()
            if dirty or (running and now - last >= .25):
                snapshot = scanner.snapshot(focus, depth)
                snapshot.update(running=running, effort=effort)
                print(json.dumps(snapshot), flush=True)
                last = now; dirty = False
            if not running:
                try:commands.put(commands.get(timeout=.1))
                except queue.Empty:pass
    finally:
        scanner.close()


if __name__ == '__main__':
    worker()
