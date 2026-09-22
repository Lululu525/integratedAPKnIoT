"""Unit tests for the credential rules the domain still owns.

Hashing and access-token signing moved to fastapi-users and are not retested
here: what is left is the part this project decides, and the part both doors
onto account creation have to agree on.
"""

from __future__ import annotations

import pytest
from domain import auth


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        ("Foo@Example.com", "foo@example.com"),
        ("  foo@example.com  ", "foo@example.com"),
        ("FOO@EXAMPLE.COM", "foo@example.com"),
    ],
    ids=["mixed-case", "surrounding-space", "shouting"],
)
def test_normalize_email_collapses_spellings_of_one_address(written, stored):
    assert auth.normalize_email(written) == stored


def test_normalize_email_is_idempotent():
    # Applied on write and again on read, so a second pass must not move it.
    once = auth.normalize_email("  Bob@Example.COM ")

    assert auth.normalize_email(once) == once


@pytest.mark.parametrize(
    "password",
    ["", "short", "x" * (auth.MAX_PASSWORD_BYTES + 1)],
    ids=["empty", "too-short", "past-the-ceiling"],
)
def test_validate_password_rejects(password):
    with pytest.raises(auth.InvalidCredentialFormat):
        auth.validate_password(password)


def test_validate_password_measures_the_ceiling_in_bytes_not_characters():
    """A multi-byte password is longer than it looks.

    The ceiling exists to bound argon2's memory cost, which scales with the
    bytes it is handed, so counting characters would let a password four times
    the intended size through.
    """
    just_over = "一" * (auth.MAX_PASSWORD_BYTES // 3 + 1)

    assert len(just_over) < auth.MAX_PASSWORD_BYTES
    with pytest.raises(auth.InvalidCredentialFormat):
        auth.validate_password(just_over)


def test_validate_password_accepts_a_usable_one():
    auth.validate_password("long-enough-pw")
