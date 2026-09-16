#!/usr/bin/python3
"""Narrow, manual macOS cache maintenance. No arbitrary deletion paths accepted."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import uuid

HOME = Path.home()
DEBUG = HOME / '.cache/diskpick/unconfigured/target/debug'
PROFILES = HOME / 'Library/Caches/ms-playwright-mcp'
GIB = 1024 ** 3
TARGET_GIB = 40
SESSION = re.compile(r's-([0-9a-z]+)-([0-9a-z]+)-([0-9a-z]+)$')
CRATE = re.compile(r'[A-Za-z0-9_-]+-[0-9a-z]+$')
CACHE_NAMES = ('Cache', 'Code Cache', 'GPUCache')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


class Unsafe(Exception):
    pass


def run(args, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result


def fingerprint(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_size,
            s.st_mtime_ns, s.st_ctime_ns)


@contextlib.contextmanager
def directory(path):
    """Resolve every component through descriptors; never follow symlinks."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise Unsafe('nonabsolute or traversing path')
    fd = os.open('/', DIR_FLAGS)
    try:
        for name in path.parts[1:]:
            nxt = os.open(name, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def child_directory(parent, parts):
    fd = os.dup(parent)
    try:
        for part in parts:
            if part in ('', '.', '..') or '/' in part:
                raise Unsafe('invalid relative component')
            nxt = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = nxt
        yield fd
    finally:
        os.close(fd)


@contextlib.contextmanager
def locked_file(path):
    """Only lock an existing regular, owned, single-link file; never create it."""
    with directory(path.parent) as parent:
        fd = os.open(path.name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        try:
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1:
                raise Unsafe('unexpected lock file')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Do not act under a replaced/unlinked lock.
            if fingerprint(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != fingerprint(s):
                raise Unsafe('lock changed')
            yield fd
        finally:
            os.close(fd)


def snapshot(fd):
    """Reject the entire tree if it contains a link, mount, device or foreign file."""
    entries = {}
    device = os.fstat(fd).st_dev
    allocated = 0

    def visit(current, prefix):
        nonlocal allocated
        s = os.fstat(current)
        if s.st_dev != device or s.st_uid != os.getuid():
            raise Unsafe('foreign owner or filesystem')
        entries[prefix] = fingerprint(s)
        for name in sorted(os.listdir(current)):
            rel = prefix + (name,)
            s = os.stat(name, dir_fd=current, follow_symlinks=False)
            if s.st_dev != device or s.st_uid != os.getuid():
                raise Unsafe('foreign owner or filesystem')
            if stat.S_ISDIR(s.st_mode):
                with child_directory(current, (name,)) as child:
                    if os.fstat(child).st_ino != s.st_ino:
                        raise Unsafe('directory replaced')
                    visit(child, rel)
            elif stat.S_ISREG(s.st_mode):
                entries[rel] = fingerprint(s)
                allocated += s.st_blocks * 512
            else:
                raise Unsafe('symlink or special file in cache')
            if len(entries) > 100000:
                raise Unsafe('unexpectedly large cache tree')

    visit(fd, ())
    return entries, allocated


def remove_contents(fd, expected, log):
    """Unlink exact snapshotted regular files; no recursive path-based removal."""
    if snapshot(fd)[0] != expected:
        raise Unsafe('tree changed before deletion')
    # The write-ahead record must reach disk before the first unlink.
    log('intent', files=len(expected), entries=[{'path': '/'.join(p), 'stat': v}
                                               for p, v in expected.items()])
    for rel, saved in sorted(expected.items(), key=lambda x: (-len(x[0]), x[0])):
        if not rel:
            continue
        with child_directory(fd, rel[:-1]) as parent:
            now = os.stat(rel[-1], dir_fd=parent, follow_symlinks=False)
            if stat.S_ISREG(saved[2]):
                if fingerprint(now) != saved:
                    raise Unsafe('file changed during deletion; remaining files preserved')
                os.unlink(rel[-1], dir_fd=parent)
            else:
                # Child deletion changes directory timestamps and sizes.
                if (now.st_dev, now.st_ino, now.st_mode, now.st_uid) != saved[:4]:
                    raise Unsafe('directory replaced during deletion')
                os.rmdir(rel[-1], dir_fd=parent)
    # Keep the approved top-level cache/session directory, avoiding rename races.


def processes():
    result = run(['/bin/ps', '-axo', 'comm='])
    if result.returncode or result.stderr:
        raise Unsafe('process inspection failed')
    commands = run(['/bin/ps', '-axo', 'command='])
    if commands.returncode or commands.stderr:
        raise Unsafe('process command inspection failed')
    return {Path(x.strip()).name for x in result.stdout.splitlines()}, commands.stdout


def idle(path, compilers=False):
    names, commands = processes()
    if compilers and names.intersection({'cargo', 'rustc', 'rustdoc'}):
        raise Unsafe('compiler/build process is running')
    if str(path) in commands:
        raise Unsafe('a running process refers to this directory')
    result = run(['/usr/sbin/lsof', '-nP', '-F', 'n', '+D', str(path)], timeout=30)
    if result.returncode != 1 or result.stdout or result.stderr:
        raise Unsafe('directory is open or open-file inspection was uncertain')


def sessions(crate):
    with directory(crate) as fd:
        found = []
        for name in os.listdir(fd):
            match = SESSION.fullmatch(name)
            if not match or name.endswith('-working'):
                continue
            s = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(s.st_mode):
                continue
            found.append((int(match.group(1), 36), name))
        return [name for stamp, name in sorted(found)]


def complete_session(path):
    with directory(path) as fd:
        for name in ('query-cache.bin', 'dep-graph.bin', 'work-products.bin'):
            s = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISREG(s.st_mode) or s.st_size == 0:
                return False
        return True


class Cleaner:
    def __init__(self, apply=False, log=None):
        self.apply = apply
        self.log = log or (lambda *a, **kw: None)
        self.candidates = 0
        self.allocated = 0
        self.skipped = 0

    def reserve_met(self):
        return False  # Explicitly selected items are processed; reserve is informational.

    def skip(self, path, exc):
        self.skipped += 1
        print('SKIP', path, '-', exc, flush=True)
        self.log('skip', path=str(path), reason=str(exc))

    def clean_tree(self, path, activity_root, age, compilers=False):
        if self.reserve_met():
            return
        with directory(path) as fd:
            before, size = snapshot(fd)
            if not any(stat.S_ISREG(v[2]) for v in before.values()):
                return
            if max(v[5] for v in before.values()) > (time.time() - age) * 1e9:
                raise Unsafe('cache has recently modified files')
            idle(activity_root, compilers)
            if snapshot(fd)[0] != before:
                raise Unsafe('tree changed while checking activity')
            self.candidates += 1
            self.allocated += size
            print(('CLEAN' if self.apply else 'WOULD CLEAN'),
                  '%.3f GiB' % (size/GIB), path, flush=True)
            self.log('candidate', path=str(path), allocated_bytes=size)
            if self.apply:
                # Check pathname identity again, then unlink through the held descriptor.
                with directory(path) as current:
                    if os.fstat(current).st_ino != os.fstat(fd).st_ino:
                        raise Unsafe('cache directory replaced')
                idle(activity_root, compilers)
                if self.reserve_met():
                    return
                remove_contents(fd, before,
                                lambda event, **kw: self.log(event, path=str(path), **kw))
                self.log('completed', path=str(path), allocated_bytes=size)

    def rust(self, debug=DEBUG):
        try:
            if not debug.exists():
                raise Unsafe('build directory absent; nothing to clean')
            names, _ = processes()
            if names.intersection({'cargo', 'rustc', 'rustdoc'}):
                raise Unsafe('compiler/build process is running')
            with locked_file(debug / '.cargo-lock'):
                incr = debug / 'incremental'
                with directory(incr) as fd:
                    crates = [n for n in os.listdir(fd) if CRATE.fullmatch(n)]
                for name in sorted(crates):
                    crate = incr / name
                    try:
                        found = sessions(crate)
                        if len(found) < 2:
                            continue
                        newest = crate / found[-1]
                        if not complete_session(newest):
                            raise Unsafe('newest session incomplete; preserve all sessions')
                        with directory(newest) as newest_fd:
                            preserved = snapshot(newest_fd)[0]
                        for old_name in found[:-1]:
                            old = crate / old_name
                            try:
                                lock = crate / ('-'.join(old_name.split('-')[:3]) + '.lock')
                                with locked_file(lock):
                                    if sessions(crate) != found:
                                        raise Unsafe('session inventory changed')
                                    with directory(newest) as check:
                                        if snapshot(check)[0] != preserved:
                                            raise Unsafe('newest session changed')
                                    self.clean_tree(old, old, 3600, compilers=True)
                            except (OSError, Unsafe, subprocess.TimeoutExpired) as exc:
                                self.skip(old, exc)
                    except (OSError, Unsafe, subprocess.TimeoutExpired, ValueError) as exc:
                        self.skip(crate, exc)
        except (OSError, Unsafe, subprocess.TimeoutExpired) as exc:
            self.skip(debug, exc)

    def browsers(self, base=PROFILES, cache_names=CACHE_NAMES):
        try:
            with directory(base) as fd:
                profiles = [n for n in os.listdir(fd) if re.fullmatch(r'mcp-chrome-[a-zA-Z0-9_-]+', n)]
            for name in sorted(profiles):
                profile = base / name
                for cache in cache_names:
                    path = profile / 'Default' / cache
                    try:
                        # Missing cache is normal; symlinks are still rejected by directory().
                        if not os.path.lexists(path):
                            continue
                        self.clean_tree(path, profile, 86400)
                    except (OSError, Unsafe, subprocess.TimeoutExpired) as exc:
                        self.skip(path, exc)
        except (OSError, Unsafe, subprocess.TimeoutExpired) as exc:
            self.skip(base, exc)


def free_bytes():
    s = os.statvfs(HOME)
    return s.f_bavail * s.f_frsize

