from __future__ import annotations

import pytest
from infrastructure.local_storage import LocalStorage


@pytest.fixture
def storage(tmp_path):
    return LocalStorage(tmp_path / "firmware")


def test_put_then_get_round_trips_data(storage):
    storage.put("v1.bin", b"binary contents")

    assert storage.get("v1.bin") == b"binary contents"


def test_put_overwrites_existing_file(storage):
    storage.put("v1.bin", b"old contents")
    storage.put("v1.bin", b"new contents")

    assert storage.get("v1.bin") == b"new contents"


def test_exists_reflects_whether_file_was_put(storage):
    assert storage.exists("v1.bin") is False

    storage.put("v1.bin", b"binary contents")

    assert storage.exists("v1.bin") is True


def test_delete_removes_file(storage):
    storage.put("v1.bin", b"binary contents")

    storage.delete("v1.bin")

    assert storage.exists("v1.bin") is False


def test_delete_missing_file_is_a_no_op(storage):
    storage.delete("never-existed.bin")


def test_get_missing_file_raises_file_not_found(storage):
    with pytest.raises(FileNotFoundError):
        storage.get("never-existed.bin")


def test_path_traversal_filename_is_confined_to_base_dir(tmp_path):
    base_dir = tmp_path / "firmware"
    storage = LocalStorage(base_dir)

    storage.put("../../../../etc/passwd", b"malicious payload")

    escaped_path = tmp_path / "etc" / "passwd"
    assert not escaped_path.exists()
    assert (base_dir / "passwd").read_bytes() == b"malicious payload"
    assert storage.get("../../../../etc/passwd") == b"malicious payload"


def test_absolute_path_filename_is_confined_to_base_dir(tmp_path):
    base_dir = tmp_path / "firmware"
    storage = LocalStorage(base_dir)

    storage.put("/etc/passwd", b"malicious payload")

    assert not (tmp_path / "etc" / "passwd").exists()
    assert (base_dir / "passwd").read_bytes() == b"malicious payload"


def test_iter_chunks_reassembles_into_the_stored_bytes(storage):
    data = bytes(range(256)) * 40
    storage.put("v1.bin", data)

    assert b"".join(storage.iter_chunks("v1.bin", chunk_size=100)) == data


def test_iter_chunks_hands_back_no_more_than_a_chunk_at_a_time(storage):
    storage.put("v1.bin", b"x" * 250)

    assert [len(c) for c in storage.iter_chunks("v1.bin", chunk_size=100)] == [100, 100, 50]


def test_iter_chunks_yields_nothing_for_an_empty_file(storage):
    storage.put("v1.bin", b"")

    assert list(storage.iter_chunks("v1.bin")) == []


def test_iter_chunks_missing_file_raises_file_not_found(storage):
    # Raised on the first `next`, not at the call, since this is a generator.
    with pytest.raises(FileNotFoundError):
        list(storage.iter_chunks("never-existed.bin"))


def test_iter_chunks_is_confined_to_base_dir(tmp_path):
    base_dir = tmp_path / "firmware"
    storage = LocalStorage(base_dir)
    storage.put("../../../../etc/passwd", b"malicious payload")

    assert b"".join(storage.iter_chunks("../../../../etc/passwd")) == b"malicious payload"
