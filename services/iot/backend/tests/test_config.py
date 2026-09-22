"""Tests for the secrets Settings reads lazily.

Key generation, TLS certs and alembic all import config before `.env` exists,
so constructing Settings must not require JWT_SECRET; only reading it, as the
server does at boot, may fail.
"""

from __future__ import annotations

import pytest
from config import Settings, get_settings
from infrastructure.db import make_engine


def test_settings_constructs_without_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)

    settings = Settings()

    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _ = settings.jwt_secret


def test_jwt_secret_read_from_env(monkeypatch):
    secret = "from-env-0123456789abcdefghijklmnopqrstuv"
    monkeypatch.setenv("JWT_SECRET", secret)

    assert Settings().jwt_secret == secret


def test_jwt_secret_rejects_short_value(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "too-short")

    with pytest.raises(RuntimeError, match="32 bytes"):
        _ = Settings().jwt_secret


def test_database_url_defaults_to_the_db_path(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_PATH", str(tmp_path / "app.db"))

    assert Settings().database_url == f"sqlite:///{tmp_path / 'app.db'}"


def test_engine_creates_the_directory_of_the_database_it_opens(monkeypatch, tmp_path):
    # DATABASE_URL wins over DB_PATH, so creating DB_PATH's parent would make
    # the directory nobody opens and skip the one that is opened.
    elsewhere = tmp_path / "elsewhere" / "app.db"
    monkeypatch.setenv("DB_PATH", str(tmp_path / "unused" / "app.db"))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{elsewhere}")
    get_settings.cache_clear()

    make_engine().connect().close()

    assert elsewhere.parent.is_dir()
    assert not (tmp_path / "unused").exists()
    get_settings.cache_clear()


def test_the_server_reads_no_signing_key(monkeypatch, tmp_path):
    """There is nothing under KEYS_DIR the server needs to boot.

    Firmware is verified against the public key on the uploading account, so a
    checkout that never ran `generate_keys.py` serves fine. Only somebody about
    to publish needs a key pair, and it is theirs, not the server's.
    """
    monkeypatch.setenv("KEYS_DIR", str(tmp_path))
    get_settings.cache_clear()

    assert not hasattr(Settings(), "read_private_key")
    get_settings.cache_clear()
