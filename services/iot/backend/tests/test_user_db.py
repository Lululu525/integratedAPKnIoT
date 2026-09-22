"""Unit tests for the account store fastapi-users reads through.

The library hands this raw user input, so this is the boundary where an address
becomes its canonical form. Everything downstream, including the unique index,
assumes it already happened here.
"""

from __future__ import annotations

import pytest
from conftest import FakeUserRepository
from domain.models import User
from infrastructure.user_db import SyncUserDatabase


@pytest.fixture
def user_db():
    return SyncUserDatabase(FakeUserRepository())


async def test_create_stores_the_normalized_address(user_db):
    created = await user_db.create({"email": "  Bob@Example.COM ", "hashed_password": "hash"})

    assert created.email == "bob@example.com"


async def test_get_by_email_normalizes_what_it_is_asked_for(user_db):
    await user_db.create({"email": "bob@example.com", "hashed_password": "hash"})

    assert await user_db.get_by_email("BOB@Example.com") is not None


async def test_get_by_email_answers_none_for_an_unknown_address(user_db):
    assert await user_db.get_by_email("nobody@example.com") is None


async def test_get_returns_the_account_by_id(user_db):
    created = await user_db.create({"email": "bob@example.com", "hashed_password": "hash"})

    assert (await user_db.get(created.id)).email == "bob@example.com"


async def test_update_writes_only_the_keys_it_was_given(user_db):
    created = await user_db.create(
        {"email": "bob@example.com", "hashed_password": "old", "is_superuser": True}
    )

    updated = await user_db.update(created, {"hashed_password": "new"})

    assert updated.hashed_password == "new"
    assert updated.is_superuser is True
    assert updated.email == "bob@example.com"


async def test_update_normalizes_a_changed_address(user_db):
    """Changing an address goes through the same door creating one does.

    Missed here, the row would hold a mixed-case address that the lookup, which
    normalizes, could never find again: the account would exist and be
    unreachable by login.
    """
    created = await user_db.create({"email": "bob@example.com", "hashed_password": "hash"})

    updated = await user_db.update(created, {"email": "Robert@Example.COM"})

    assert updated.email == "robert@example.com"
    assert await user_db.get_by_email("robert@example.com") is not None


async def test_delete_removes_the_account(user_db):
    created = await user_db.create({"email": "bob@example.com", "hashed_password": "hash"})

    await user_db.delete(created)

    assert await user_db.get(created.id) is None


async def test_create_passes_the_protocol_fields_through(user_db):
    """Anything the library sets on creation has to survive the mapping.

    A dataclass that quietly dropped one of these would leave every new account
    with the default, which for `is_superuser` is the difference between an
    admin and an operator.
    """
    created = await user_db.create(
        {
            "email": "bob@example.com",
            "hashed_password": "hash",
            "is_active": False,
            "is_superuser": True,
            "is_verified": True,
        }
    )

    assert (created.is_active, created.is_superuser, created.is_verified) == (
        False,
        True,
        True,
    )
    assert isinstance(created, User)
