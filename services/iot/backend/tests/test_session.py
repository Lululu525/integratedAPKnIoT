"""Unit tests for refresh-handle rotation.

fastapi-users has no refresh token, so none of this is library behaviour being
retested: `application/session.py` is where a dashboard session outliving its
access token is decided, and every rule below is one this project wrote.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from application.session import InvalidRefreshToken, Session
from conftest import FakeRefreshTokenRepository, FakeUserRepository
from domain.models import User

EXPIRES_DAYS = 14


def make_session(users=None, tokens=None):
    users = users or FakeUserRepository()
    tokens = tokens or FakeRefreshTokenRepository()
    return Session(tokens, users, EXPIRES_DAYS), users, tokens


def seed(users: FakeUserRepository, email="bob@example.com", is_active=True) -> User:
    return users.add(User(email=email, hashed_password="hash", is_active=is_active))


def test_start_issues_a_handle_bound_to_the_account():
    session, users, tokens = make_session()
    user = seed(users)

    handle = session.start(user)

    assert tokens.get(handle).user_id == user.id


def test_two_logins_get_different_handles():
    """One per session, so logging out of one machine leaves the other alone."""
    session, users, tokens = make_session()
    user = seed(users)

    first = session.start(user)
    second = session.start(user)

    assert first != second
    assert tokens.get(first) is not None
    assert tokens.get(second) is not None


def test_renew_returns_the_account_and_a_new_handle():
    session, users, _ = make_session()
    user = seed(users)
    handle = session.start(user)

    renewed = session.renew(handle)

    assert renewed.user.id == user.id
    assert renewed.refresh_token != handle


def test_renew_destroys_the_handle_it_was_given():
    """Single use is the whole defence. A copy is good for one renewal, and only
    until the real client renews first."""
    session, users, tokens = make_session()
    handle = session.start(seed(users))

    session.renew(handle)

    assert tokens.get(handle) is None


def test_renewing_the_same_handle_twice_is_rejected():
    session, users, _ = make_session()
    handle = session.start(seed(users))
    session.renew(handle)

    with pytest.raises(InvalidRefreshToken):
        session.renew(handle)


def test_renew_rejects_a_handle_that_was_never_issued():
    session, _, _ = make_session()

    with pytest.raises(InvalidRefreshToken):
        session.renew("not-a-handle")


def test_renew_rejects_and_clears_an_aged_out_handle():
    session, users, tokens = make_session()
    handle = session.start(seed(users))
    tokens.get(handle).created_at = datetime.now(timezone.utc) - timedelta(
        days=EXPIRES_DAYS, seconds=1
    )

    with pytest.raises(InvalidRefreshToken):
        session.renew(handle)
    assert tokens.get(handle) is None


def test_a_handle_one_second_inside_the_window_still_renews():
    session, users, tokens = make_session()
    handle = session.start(seed(users))
    tokens.get(handle).created_at = datetime.now(timezone.utc) - timedelta(
        days=EXPIRES_DAYS, seconds=-1
    )

    assert session.renew(handle).user.id is not None


def test_renew_refuses_a_disabled_account_and_drops_its_handles():
    """Disabling an account has to end the sessions it already had.

    Otherwise it only stops new logins, and whoever was already signed in keeps
    renewing indefinitely.
    """
    session, users, tokens = make_session()
    user = seed(users)
    first = session.start(user)
    second = session.start(user)
    user.is_active = False

    with pytest.raises(InvalidRefreshToken):
        session.renew(first)
    assert tokens.get(second) is None


def test_renew_refuses_a_handle_whose_account_is_gone():
    session, users, _ = make_session()
    user = seed(users)
    handle = session.start(user)
    users.delete(user)

    with pytest.raises(InvalidRefreshToken):
        session.renew(handle)


def test_end_drops_only_the_handle_it_was_given():
    session, users, tokens = make_session()
    user = seed(users)
    laptop = session.start(user)
    phone = session.start(user)

    session.end(laptop)

    assert tokens.get(laptop) is None
    assert tokens.get(phone) is not None


def test_end_is_silent_on_a_handle_that_is_already_gone():
    """A double logout races to the same end state rather than to an error."""
    session, users, _ = make_session()
    handle = session.start(seed(users))
    session.end(handle)

    session.end(handle)


def test_end_all_drops_every_handle_the_account_holds():
    session, users, tokens = make_session()
    user = seed(users)
    laptop = session.start(user)
    phone = session.start(user)

    session.end_all(user)

    assert tokens.get(laptop) is None
    assert tokens.get(phone) is None


def test_end_all_leaves_another_account_alone():
    session, users, tokens = make_session()
    mine = session.start(seed(users, "bob@example.com"))
    theirs = session.start(seed(users, "eve@example.com"))

    session.end_all(users.get_by_email("bob@example.com"))

    assert tokens.get(mine) is None
    assert tokens.get(theirs) is not None
