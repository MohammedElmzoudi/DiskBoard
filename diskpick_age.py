"""Explicit folder groups: preview age-matched files, then unlink reviewed files only."""
import contextlib
import os
from pathlib import Path
import stat
import subprocess
import time

import diskpick_engine as engine

MAX_ENTRIES = 100000
MAX_SECONDS = 10


def identity(st):
    return [st.st_dev, st.st_ino, st.st_mode, st.st_uid]


def validate_root(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Choose an absolute folder path without traversal.')
    home = Path.home()
    protected = ('/System', '/Library', '/usr', '/bin', '/sbin', '/private/etc',
                 '/private/var/db', '/private/var/vm', '/private/var/root')
    if (path == home or len(path.parts) < 3 or
            any(path == Path(base) or Path(base) in path.parents for base in protected) or
            any(part == '.git' or part.endswith(('.app', '.photoslibrary')) for part in path.parts)):
        raise ValueError('Choose a specific personal folder, not Home, system data, Git metadata, or an app/library bundle.')
    return path


def has_git(fd):
    try:
        os.stat('.git', dir_fd=fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def inspect(area):
    started = time.monotonic()
    cutoff = time.time_ns() - area['older_than_days'] * 86400 * 10**9
    candidates = []
    reasons = []
    paths = []
    seen = set()
    inspected = kept = total = 0
    for value in area['roots']:
        root_candidates = []
        root_bytes = 0
        try:
            root = validate_root(value)
            with engine.directory(root) as rootfd:
                rootstat = os.fstat(rootfd)
                if rootstat.st_uid != os.getuid():
                    raise engine.Unsafe('Folder belongs to another user')
                # A checkout needs its dedicated retirement checks, not an age rule.
                if has_git(rootfd):
                    raise engine.Unsafe('Git checkout: choose an output folder inside it instead')

                def visit(fd, rel, parents):
                    nonlocal inspected, kept, root_bytes
                    with os.scandir(fd) as entries:
                        for entry in entries:
                            inspected += 1
                            if inspected > MAX_ENTRIES or time.monotonic() - started > MAX_SECONDS:
                                raise engine.Unsafe('Inspection limit reached; choose smaller folders')
                            path = root.joinpath(*rel, entry.name)
                            st = entry.stat(follow_symlinks=False)
                            if st.st_dev != rootstat.st_dev or st.st_uid != os.getuid():
                                kept += 1; continue
                            if stat.S_ISDIR(st.st_mode):
                                if not area.get('recursive', True) or entry.name == '.git' or entry.name.endswith(('.app', '.photoslibrary')):
                                    kept += 1; continue
                                if len(rel) >= 50:
                                    raise engine.Unsafe('Folder nesting limit reached; choose a deeper folder')
                                with engine.child_directory(fd, (entry.name,)) as child:
                                    if identity(os.fstat(child)) != identity(st):
                                        raise engine.Unsafe('Folder changed during inspection')
                                    if has_git(child):
                                        kept += 1; continue
                                    visit(child, rel + (entry.name,), parents + [identity(st)])
                            elif stat.S_ISREG(st.st_mode):
                                root_bytes += st.st_blocks * 512
                                if st.st_nlink != 1 or st.st_mtime_ns >= cutoff:
                                    kept += 1; continue
                                key = (st.st_dev, st.st_ino)
                                if key not in seen:
                                    seen.add(key)
                                    root_candidates.append(dict(path=str(path), root=str(root),
                                        allocated_bytes=st.st_blocks * 512,
                                        fingerprint=list(engine.fingerprint(st)), parents=parents))
                            else:
                                kept += 1
                visit(rootfd, (), [identity(rootstat)])
            candidates.extend(root_candidates)
            paths.append(dict(path=str(root), bytes=root_bytes))
            total += root_bytes
        except (OSError, ValueError, engine.Unsafe) as exc:
            reasons.append(str(value) + ': ' + str(exc) + '. This folder is kept.')
    eligible = sum(p['allocated_bytes'] for p in candidates)
    if kept:
        reasons.append('%d recent, linked, excluded, or out-of-scope entries kept.' % kept)
    return dict(area=area, total_bytes=total, eligible_bytes=eligible,
                candidates=len(candidates), preview=candidates, paths=paths, reasons=reasons,
                status='READY' if candidates else 'KEPT', inspected=inspected)


def execute(area, cleaner):
    approved = getattr(cleaner, 'allowed_paths', None)
    if not cleaner.apply:
        row = inspect(area)
        for item in row['preview']:
            cleaner.candidates += 1
            cleaner.allocated += item['allocated_bytes']
            cleaner.log('candidate', **item)
        for reason in row['reasons']:
            cleaner.skip(area['title'], reason)
        return
    if not isinstance(approved, dict):
        cleaner.skip(area['title'], 'Age-based cleanup requires an exact interactive file preview')
        return
    cutoff = time.time_ns() - area['older_than_days'] * 86400 * 10**9
    roots = {str(validate_root(p)) for p in area['roots']}
    # Check activity once per root immediately before that root's reviewed batch.
    for root in sorted(roots):
        items = [p for p in approved.values() if p and p.get('root') == root]
        if not items:
            continue
        try:
            with engine.directory(root) as rootfd:
                if has_git(rootfd):
                    raise engine.Unsafe('Folder is now a Git checkout')
                engine.idle(Path(root))
                for item in items:
                    path = Path(item['path'])
                    try:
                        rel = path.relative_to(root).parts
                        if not rel or any(p in ('', '.', '..', '.git') for p in rel):
                            raise engine.Unsafe('Invalid reviewed file')
                        if not area.get('recursive', True) and len(rel) != 1:
                            raise engine.Unsafe('Subfolders are excluded by this rule')
                        with contextlib.ExitStack() as stack:
                            fd = rootfd
                            if identity(os.fstat(fd)) != item['parents'][0]:
                                raise engine.Unsafe('Root changed since preview')
                            for i, part in enumerate(rel[:-1], 1):
                                fd = stack.enter_context(engine.child_directory(fd, (part,)))
                                if identity(os.fstat(fd)) != item['parents'][i] or has_git(fd):
                                    raise engine.Unsafe('Folder changed since preview')
                            before = os.stat(rel[-1], dir_fd=fd, follow_symlinks=False)
                            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                                    before.st_mtime_ns >= cutoff or before.st_uid != os.getuid() or
                                    list(engine.fingerprint(before)) != item['fingerprint']):
                                raise engine.Unsafe('File changed since preview')
                            with engine.directory(path.parent) as named_parent:
                                if identity(os.fstat(named_parent)) != identity(os.fstat(fd)):
                                    raise engine.Unsafe('Folder moved since preview')
                            cleaner.log('intent', path=str(path), fingerprint=item['fingerprint'])
                            # Recheck after the durable audit write, before the unlink.
                            if engine.fingerprint(os.stat(rel[-1], dir_fd=fd, follow_symlinks=False)) != engine.fingerprint(before):
                                raise engine.Unsafe('File changed during cleanup')
                            os.unlink(rel[-1], dir_fd=fd)
                            cleaner.candidates += 1
                            cleaner.allocated += item['allocated_bytes']
                            cleaner.log('completed', path=str(path), allocated_bytes=item['allocated_bytes'])
                    except (OSError, ValueError, engine.Unsafe, KeyError, IndexError) as exc:
                        cleaner.skip(path, exc)
        except (OSError, engine.Unsafe, subprocess.TimeoutExpired) as exc:
            cleaner.skip(root, exc)
