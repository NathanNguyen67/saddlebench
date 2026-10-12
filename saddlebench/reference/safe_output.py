"""Atomic, no-follow directory publication through pinned Linux dirfds.

Output containment is validated against the physical parent inode. No caller-
provided path is dereferenced to write source data after validation. Prefer
renameat2(RENAME_NOREPLACE); on filesystems rejecting that Linux extension
with EINVAL/ENOSYS/EOPNOTSUPP, use an exclusively claimed directory and
fd-pinned O_EXCL copies. A fully written internal manifest is made visible by
an atomic, no-replace relative symlink named manifest.json. This supports
Lustre configurations that reject both renameat2 and hard links. Never
replace an existing output file or expose an incomplete manifest.
"""
from __future__ import annotations
import ctypes
import errno
import os
from pathlib import Path
import secrets
import shutil
import stat

_O_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_RENAME_NOREPLACE = 1


def _inside(candidate: Path, root: Path) -> bool:
    return candidate == root or root in candidate.parents


def no_symlink_components(path: Path):
    path = path.absolute()
    current = Path('/')
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ValueError(f'UNTRUSTED_OUTPUT_SYMLINK_ANCESTOR: {current}')


def _rename_noreplace(source, target, directory_fd):
    libc = ctypes.CDLL(None, use_errno=True)
    fn = getattr(libc, 'renameat2', None)
    if fn is None:
        raise OSError(errno.ENOSYS, 'renameat2 RENAME_NOREPLACE unavailable')
    fn.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    fn.restype = ctypes.c_int
    if fn(directory_fd, os.fsencode(source), directory_fd, os.fsencode(target), _RENAME_NOREPLACE) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


class AtomicDirectory:
    """Create files in a pinned, private temp directory, then publish once.

    Rechecks ancestor paths and the *opened physical* parent against protected
    producer roots immediately before commit. A source-root symlink or a mutable
    output ancestor never causes a write by dereferencing the untrusted path.
    """
    def __init__(self, destination, *, protected_roots=(), create_parents=False, prefix='.sbe-'):
        self.destination = Path(destination).absolute()
        self.protected_roots = tuple(Path(p).resolve() for p in protected_roots)
        self.create_parents = create_parents
        self.prefix = prefix
        self.parent_fd = None
        self.directory_fd = None
        self.temp_name = None
        self.committed = False

    def _checked_parent(self):
        fd = os.open('/', _O_DIR)
        try:
            current = Path('/')
            for component in self.destination.parent.parts[1:]:
                prospective = current / component
                if any(_inside(prospective, p) for p in self.protected_roots):
                    raise ValueError('REFUSE_WRITE_INSIDE_EVIDENCE_OR_PRODUCTION_TREE')
                try:
                    next_fd = os.open(component, _O_DIR, dir_fd=fd)
                except FileNotFoundError:
                    if not self.create_parents:
                        raise
                    os.mkdir(component, 0o700, dir_fd=fd)
                    next_fd = os.open(component, _O_DIR, dir_fd=fd)
                os.close(fd)
                fd = next_fd
                current = Path(os.readlink(f'/proc/self/fd/{fd}'))
                if current.name.endswith(' (deleted)'):
                    raise ValueError('OUTPUT_PARENT_RENAMED_DURING_OPEN')
                if any(_inside(current, p) for p in self.protected_roots):
                    raise ValueError('REFUSE_WRITE_INSIDE_EVIDENCE_OR_PRODUCTION_TREE')
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _verify_locator(self):
        no_symlink_components(self.destination.parent)
        actual = os.stat(self.destination.parent, follow_symlinks=False)
        opened = os.fstat(self.parent_fd)
        if (actual.st_dev, actual.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError('OUTPUT_PARENT_LOCATOR_CHANGED')
        real = Path(os.readlink(f'/proc/self/fd/{self.parent_fd}'))
        if real.name.endswith(' (deleted)') or any(_inside(real, p) for p in self.protected_roots):
            raise ValueError('OUTPUT_PARENT_MOVED_OR_PROTECTED')

    def __enter__(self):
        no_symlink_components(self.destination)
        self.parent_fd = self._checked_parent()
        try:
            self._verify_locator()
            try:
                os.stat(self.destination.name, dir_fd=self.parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError('REFERENCE_ALREADY_EXISTS')
            self.temp_name = self.prefix + secrets.token_hex(12)
            os.mkdir(self.temp_name, 0o700, dir_fd=self.parent_fd)
            self.directory_fd = os.open(self.temp_name, _O_DIR, dir_fd=self.parent_fd)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    @property
    def path(self):
        return Path(f'/proc/self/fd/{self.directory_fd}')

    def write_bytes(self, name, content):
        if name in ('', '.', '..') or '/' in name or '\\' in name:
            raise ValueError('INVALID_OUTPUT_FILENAME')
        fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                     dir_fd=self.directory_fd)
        with os.fdopen(fd, 'wb') as out:
            out.write(content)

    def _verify_claimed_directory(self, claimed_fd):
        """Verify the exclusively-created destination still names our open inode.

        All fallback writes are through pinned dirfds. If another process moves
        a directory, it cannot redirect a write via a symlink or path traversal.
        """
        self._verify_locator()
        expected = os.fstat(claimed_fd)
        actual = os.stat(self.destination.name, dir_fd=self.parent_fd,
                         follow_symlinks=False)
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise ValueError('OUTPUT_DESTINATION_LOCATOR_CHANGED')
        real = Path(os.readlink(f'/proc/self/fd/{claimed_fd}'))
        if real.name.endswith(' (deleted)') or any(_inside(real, p) for p in self.protected_roots):
            raise ValueError('OUTPUT_DESTINATION_MOVED_OR_PROTECTED')

    def _copy_exclusive(self, source_name, published_name, dest_fd):
        """Copy a private staged regular file to a claimed directory, never replacing.

        Source and target opens are strictly relative to pinned directory fds.
        An error leaves at worst an incomplete unpublished destination.
        """
        self._verify_claimed_directory(dest_fd)
        read_fd = os.open(source_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                          dir_fd=self.directory_fd)
        try:
            original = os.fstat(read_fd)
            if not stat.S_ISREG(original.st_mode):
                raise ValueError('STAGED_OUTPUT_NOT_REGULAR_FILE')
            write_fd = os.open(published_name,
                               os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                               os.O_NOFOLLOW | os.O_CLOEXEC,
                               0o600, dir_fd=dest_fd)
            try:
                # Using fdopen and exclusive fd-relative create avoids both
                # hard links (EPERM on LS6) and overwrite-capable rename().
                with os.fdopen(read_fd, 'rb', closefd=False) as source:
                    with os.fdopen(write_fd, 'wb', closefd=False) as target:
                        shutil.copyfileobj(source, target, length=1024 * 1024)
                        target.flush()
                        os.fsync(write_fd)
                written = os.fstat(write_fd)
                if written.st_size != original.st_size:
                    raise OSError(errno.EIO, 'INCOMPLETE_STAGED_FILE_COPY')
            finally:
                os.close(write_fd)
        finally:
            os.close(read_fd)
        self._verify_claimed_directory(dest_fd)

    def _commit_manifest_last(self):
        """Lustre-safe publication without renameat2 or hard links.

        O_EXCL file creation is no-overwrite. The manifest is first copied to
        a private regular file inside the exclusively created destination.
        Finally, an atomic, no-replace *relative* symlink named manifest.json
        reveals those fully written bytes. Existing readers see exactly the
        original JSON manifest contents. The symlink can only point within our
        verified destination directory and is never created before data files.

        This is manifest-last readiness rather than an atomic directory rename.
        Failed attempts preserve incomplete destinations without a manifest.
        """
        names = os.listdir(self.directory_fd)
        if 'manifest.json' not in names:
            raise ValueError('MANIFEST_REQUIRED_FOR_FALLBACK_COMMIT')
        ordered = sorted(name for name in names if name != 'manifest.json')
        for name in names:
            if name in ('', '.', '..') or '/' in name or chr(92) in name:
                raise ValueError('INVALID_STAGED_OUTPUT_NAME')
            item = os.stat(name, dir_fd=self.directory_fd, follow_symlinks=False)
            if not stat.S_ISREG(item.st_mode):
                raise ValueError('STAGED_OUTPUT_NOT_REGULAR_FILE')
        self._verify_locator()
        os.mkdir(self.destination.name, 0o700, dir_fd=self.parent_fd)
        dest_fd = os.open(self.destination.name, _O_DIR, dir_fd=self.parent_fd)
        try:
            self._verify_claimed_directory(dest_fd)
            for name in ordered:
                self._copy_exclusive(name, name, dest_fd)
            hidden_manifest = '.sbe-manifest-' + secrets.token_hex(12)
            self._copy_exclusive('manifest.json', hidden_manifest, dest_fd)
            self._verify_claimed_directory(dest_fd)
            # An atomic exclusive symlink create publishes the complete
            # manifest in one operation (unlike O_EXCL then streaming bytes).
            # Only a trusted relative basename is used; no path traversal.
            os.symlink(hidden_manifest, 'manifest.json', dir_fd=dest_fd)
            self._verify_claimed_directory(dest_fd)
        finally:
            os.close(dest_fd)
        self.committed = True
        # Clean only our own private staging directory. Never remove the
        # published destination, including if an earlier step failed.
        for name in names:
            os.unlink(name, dir_fd=self.directory_fd)
        os.rmdir(self.temp_name, dir_fd=self.parent_fd)
        self.temp_name = None
        return self.destination

    def commit(self):
        if self.committed:
            raise ValueError('ALREADY_COMMITTED')
        self._verify_locator()
        try:
            _rename_noreplace(self.temp_name, self.destination.name, self.parent_fd)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP):
                raise
            # Filesystems without renameat2(RENAME_NOREPLACE) must never fall
            # back to hard links (EPERM on LS6) or overwrite-capable rename().
            return self._commit_manifest_last()
        self.committed = True
        self._verify_locator()
        own = os.fstat(self.directory_fd)
        written = os.stat(self.destination, follow_symlinks=False)
        if (own.st_dev, own.st_ino) != (written.st_dev, written.st_ino):
            raise ValueError('OUTPUT_LOCATOR_CHANGED_AFTER_COMMIT')
        return self.destination

    def __exit__(self, exc_type, exc, tb):
        if self.directory_fd is not None:
            if not self.committed:
                for name in os.listdir(self.directory_fd):
                    os.unlink(name, dir_fd=self.directory_fd)
            os.close(self.directory_fd)
            self.directory_fd = None
        if self.parent_fd is not None:
            if self.temp_name and not self.committed:
                try:
                    os.rmdir(self.temp_name, dir_fd=self.parent_fd)
                except FileNotFoundError:
                    pass
            os.close(self.parent_fd)
            self.parent_fd = None
