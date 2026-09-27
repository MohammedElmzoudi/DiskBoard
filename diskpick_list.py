"""Private, persistent UI list. Adding a folder grants inventory access only."""
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

import diskpick_catalog as catalog
import diskpick_engine as engine
import diskpick_setup as setup

MAX_BYTES = 256 * 1024


def folder_area(value, title=None):
    value = value.strip()
    if len(value) > 1 and value[0] == value[-1] and value[0] in ('"', "'"):
        value = value[1:-1]
    path = Path(value).expanduser()
    if not value or not value.isprintable() or not path.is_absolute():
        raise ValueError('Enter an absolute folder path, or a path starting with ~/.')
    # Match the inventory engine: no symlink components or traversal.
    with engine.directory(path):
        pass
    area = dict(id='folder-' + hashlib.sha256(str(path).encode()).hexdigest()[:16],
                title=title or path.name or 'Filesystem', kind='inspect', roots=[str(path)],
                description='Size monitor only. Files in this folder cannot be removed here.')
    catalog.validate(dict(version=1, areas=[area]))
    return area


def read_at(fd, name):
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    with os.fdopen(source, 'rb') as stream:
        st = os.fstat(stream.fileno())
        if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                st.st_nlink != 1 or st.st_mode & 0o022 or st.st_size > MAX_BYTES):
            raise ValueError('The saved list must be a private, regular file under 256 KiB.')
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('The saved list is too large.')
    return raw


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate field in the saved list.')
            result[key] = value
        return result
    try:
        data = json.loads(raw, object_pairs_hook=unique)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError('The saved list could not be read.') from exc
    if not isinstance(data, dict) or set(data) != {'version', 'areas'}:
        raise ValueError('Unsupported saved-list format.')
    return catalog.validate(data)


class SavedList:
    def __init__(self, areas, config=None, demo=False):
        self.path = (Path(config).absolute().with_suffix('.list.json') if config else
                     catalog.CONFIG.with_name('list.json'))
        self.demo = demo
        self.raw = None
        if not demo:
            try:
                with engine.directory(self.path.parent) as fd:
                    self.raw = read_at(fd, self.path.name)
            except FileNotFoundError:
                pass
        self.areas = copy.deepcopy(decode(self.raw) if self.raw is not None else areas)
        catalog.validate(dict(version=1, areas=self.areas))

    def replace(self, areas):
        data = dict(version=1, areas=copy.deepcopy(areas))
        catalog.validate(data)
        raw = (json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode()
        if len(raw) > MAX_BYTES:
            raise ValueError('The saved list is full.')
        if not self.demo:
            with setup.settings_directory(self.path.parent) as fd:
                lock = os.open('.' + self.path.name + '.lock',
                               os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                               0o600, dir_fd=fd)
                try:
                    st = os.fstat(lock)
                    if (not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or
                            st.st_nlink != 1 or st.st_mode & 0o022):
                        raise ValueError('Unexpected saved-list lock.')
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    if read_at(fd, self.path.name) != self.raw:
                        raise ValueError('Your list changed in another window. Reopen DiskBoard before editing it.')
                    name = '.list-' + uuid.uuid4().hex + '.tmp'
                    output = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=fd)
                    try:
                        with os.fdopen(output, 'wb') as stream:
                            stream.write(raw)
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.rename(name, self.path.name, src_dir_fd=fd, dst_dir_fd=fd)
                        os.fsync(fd)
                    finally:
                        try:
                            os.unlink(name, dir_fd=fd)
                        except FileNotFoundError:
                            pass
                finally:
                    os.close(lock)
        self.raw = raw
        self.areas = data['areas']

    def add(self, area):
        for existing in self.areas:
            if existing['id'] == area['id'] or (existing['kind'] == area['kind'] == 'inspect' and
                    [str(Path(p).expanduser()) for p in existing.get('roots', [])] == area.get('roots')):
                raise ValueError('This item is already in your list.')
        self.replace(self.areas + [area])

    def restore(self, removed, position):
        """Undo one removal without discarding later adds or renames."""
        if any(a['id'] == removed['id'] for a in self.areas):
            raise ValueError('This item is already in your list.')
        areas = list(self.areas)
        areas.insert(min(position, len(areas)), removed)
        self.replace(areas)
