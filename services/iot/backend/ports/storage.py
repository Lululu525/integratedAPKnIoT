"""Interface for storing and retrieving firmware binary files.

Five operations over a filename: put, get, iter_chunks, delete, exists.
`infrastructure/local_storage.py` implements it against the local disk.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

# Small enough that a fleet downloading at once costs a bounded amount of
# memory, large enough that a 1 MB image is not thousands of reads.
CHUNK_SIZE = 64 * 1024


class StorageBackend(ABC):
    @abstractmethod
    def put(self, filename: str, data: bytes) -> None:
        """Store `data` under `filename`, overwriting any existing object."""

    @abstractmethod
    def get(self, filename: str) -> bytes:
        """Return the bytes stored under `filename`. Raises FileNotFoundError if absent."""

    @abstractmethod
    def iter_chunks(self, filename: str, chunk_size: int = CHUNK_SIZE) -> Iterator[bytes]:
        """Yield the object's bytes in pieces. Raises FileNotFoundError if absent.

        The whole-file `get` stays for callers that need the bytes in hand, such
        as hashing an upload. This exists for the ones that only pass them
        through, where holding a copy per in-flight request is what hurts.
        """

    @abstractmethod
    def delete(self, filename: str) -> None:
        """Remove `filename` if present; a no-op when it does not exist."""

    @abstractmethod
    def exists(self, filename: str) -> bool:
        """Return whether `filename` is present in the store."""
