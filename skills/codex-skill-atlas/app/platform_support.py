"""Small standard-library adapters for native Windows and POSIX hosts."""
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import time


def is_link(path):
    """Include Windows junctions/reparse points on Python versions before is_junction."""
    try:
        info = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


@contextmanager
def file_lock(path):
    path = Path(path)
    if is_link(path):
        raise OSError('Lock file must not be a link')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as file:
        if is_link(path) or not os.path.samestat(os.fstat(file.fileno()), path.stat()):
            raise OSError('Lock file changed while opening')
        if os.name == 'nt':
            import msvcrt
            while True:
                file.seek(0)
                try:
                    # Byte-range locks may extend past EOF; no shared lock-file writes needed.
                    msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                        raise
                    time.sleep(.05)
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)


def background_options():
    if os.name == 'nt':
        import subprocess
        return {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def path_key(path):
    return os.path.normcase(os.path.normpath(str(path)))
