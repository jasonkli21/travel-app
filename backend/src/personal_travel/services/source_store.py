"""Opaque local private source storage, never beneath a served root."""

from __future__ import annotations

import os
import re
import secrets
import stat
from collections.abc import Iterator
from pathlib import Path

KEY = re.compile(r"^[0-9a-f]{32}$")
MAX_SOURCE_BYTES = 10 * 1024 * 1024


class LocalSourceStore:
    """Store private bytes by opaque key under one pinned, mode-0700 directory."""

    def __init__(self, root: str | Path) -> None:
        path = Path(root).expanduser()
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("Source storage requires an absolute non-symlink directory.")
        resolved = path.resolve()
        project_root = Path(__file__).resolve().parents[4]
        if resolved == project_root or project_root in resolved.parents:
            raise ValueError("Source storage must be outside the application tree.")
        existed = path.exists()
        if existed:
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError("Source storage directory must be a private 0700 directory.")
            if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
                raise ValueError("Source storage directory must be owned by the service user.")
        else:
            path.mkdir(mode=0o700, parents=True)
            os.chmod(path, 0o700)

        self.root = path
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        self.dirfd = os.open(path, flags)
        info = os.fstat(self.dirfd)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(self.dirfd)
            raise ValueError("Source storage directory must be a private 0700 directory.")
        if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
            os.close(self.dirfd)
            raise ValueError("Source storage directory must be owned by the service user.")

    def new_key(self) -> str:
        return secrets.token_hex(16)

    @staticmethod
    def validate_key(key: str) -> str:
        if not KEY.fullmatch(key):
            raise ValueError("Invalid object key.")
        return key

    def temp_path(self, key: str) -> Path:
        """Return the private temp path for the isolated local PDF worker."""
        return self.root / (self.validate_key(key) + ".tmp")

    def open_temp(self, key: str) -> int:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(self.validate_key(key) + ".tmp", flags, 0o600, dir_fd=self.dirfd)
        try:
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Source temp entry is not a regular file.")
        except BaseException:
            os.close(fd)
            self.delete(key, temp=True)
            raise
        return fd

    def write_temp(self, key: str, data: bytes) -> None:
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("Source bytes exceed the configured size limit.")
        fd = self.open_temp(key)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            self.delete(key, temp=True)
            raise

    def promote(self, key: str) -> None:
        """Atomically publish a complete temp file without replacing an object."""
        name = self.validate_key(key)
        os.link(
            name + ".tmp",
            name,
            src_dir_fd=self.dirfd,
            dst_dir_fd=self.dirfd,
            follow_symlinks=False,
        )
        os.unlink(name + ".tmp", dir_fd=self.dirfd)
        os.fsync(self.dirfd)

    def _open_read(self, key: str, *, temp: bool = False) -> int:
        name = self.validate_key(key) + (".tmp" if temp else "")
        flags = os.O_RDONLY | os.O_NOFOLLOW
        flags |= getattr(os, "O_CLOEXEC", 0)
        fd = os.open(name, flags, dir_fd=self.dirfd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600:
            os.close(fd)
            raise ValueError("Source object is not a private regular file.")
        if info.st_size > MAX_SOURCE_BYTES:
            os.close(fd)
            raise ValueError("Source object exceeds the configured size limit.")
        return fd

    def read(self, key: str, *, temp: bool = False) -> bytes:
        fd = self._open_read(key, temp=temp)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(MAX_SOURCE_BYTES + 1)
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("Source object exceeds the configured size limit.")
        return data

    def exists(self, key: str) -> bool:
        try:
            fd = self._open_read(key)
        except FileNotFoundError:
            return False
        else:
            os.close(fd)
            return True

    def entry_stats(self) -> Iterator[tuple[str, os.stat_result]]:
        """Iterate entries without following links, for the bounded cleanup sweep."""
        with os.scandir(self.dirfd) as entries:
            for entry in entries:
                name = entry.name
                try:
                    info = os.stat(name, dir_fd=self.dirfd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                yield name, info

    def delete(self, key: str, *, temp: bool = False) -> None:
        name = self.validate_key(key) + (".tmp" if temp else "")
        try:
            os.unlink(name, dir_fd=self.dirfd)
        except FileNotFoundError:
            pass

    def close(self) -> None:
        if self.dirfd >= 0:
            os.close(self.dirfd)
            self.dirfd = -1
