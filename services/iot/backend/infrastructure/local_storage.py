"""Stores firmware binaries as plain files in a local directory.

Implements the storage interface from `ports/storage.py`. Filenames are reduced
to their basename so an upload cannot write outside the configured directory.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from ports.storage import CHUNK_SIZE, StorageBackend


class LocalStorage(StorageBackend):
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, filename: str) -> Path:
        # Guard against path traversal: store flat, by basename only.
        safe = Path(filename).name
        return self._base_dir / safe

    def put(self, filename: str, data: bytes) -> None:
        self._path(filename).write_bytes(data)

    def get(self, filename: str) -> bytes:
        return self._path(filename).read_bytes()

    def iter_chunks(self, filename: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        # The handle is opened and closed inside this generator, so the port
        # never hands a caller a resource it has to remember to release.
        with self._path(filename).open("rb") as handle:
            while chunk := handle.read(chunk_size):
                yield chunk

    def delete(self, filename: str) -> None:
        self._path(filename).unlink(missing_ok=True)

    def exists(self, filename: str) -> bool:
        return self._path(filename).exists()
