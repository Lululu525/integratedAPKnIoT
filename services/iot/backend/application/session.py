"""Starting, renewing and ending a dashboard session.

The access token is a short-lived JWT that every request carries; fastapi-users
mints and verifies it. What lives here is the handle that outlives it, which is
the part the library does not provide: a session that ends the moment its
access token expires is a login form in the middle of an upload.

Rotation is the whole design. A handle is single-use, so presenting one both
issues a replacement and destroys the original. A copy taken off a machine is
therefore usable exactly once, and only until the real client renews, at which
point the thief's handle is already gone and the legitimate one keeps working.
The alternative, a long-lived handle accepted repeatedly, is a password with a
longer name.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from domain.models import RefreshToken, User
from ports.repository import RefreshTokenRepository, UserRepository

# 32 bytes of urandom, base64url encoded. The handle is a bearer credential
# with no structure to verify, so its only defence is being unguessable.
TOKEN_BYTES = 32


class InvalidRefreshToken(Exception):
    """The handle was never issued, has already been used, or has aged out."""


@dataclass
class RenewedSession:
    user: User
    refresh_token: str


class Session:
    def __init__(
        self,
        tokens: RefreshTokenRepository,
        users: UserRepository,
        expires_days: int,
    ) -> None:
        self._tokens = tokens
        self._users = users
        self._expires_days = expires_days

    def start(self, user: User) -> str:
        """Issue the first handle of a session, after a successful login."""
        assert user.id is not None
        return self._issue(user.id)

    def renew(self, handle: str) -> RenewedSession:
        """Trade a handle for its replacement and the account it belongs to."""
        stored = self._tokens.get(handle)
        if stored is None:
            # Indistinguishable from a replay of a handle that was already
            # rotated away, which is what a stolen one looks like after the
            # real client renews. Nothing more can be done about it here: the
            # row is gone, so there is no account left to revoke. Detecting
            # that case needs the used handles kept around, which is a
            # different design and not one this project needs yet.
            raise InvalidRefreshToken("unknown or already used")

        if self._expired(stored):
            self._tokens.delete(handle)
            raise InvalidRefreshToken("expired")

        user = self._users.get_by_id(stored.user_id)
        if user is None or not user.is_active:
            self._tokens.delete_for_user(stored.user_id)
            raise InvalidRefreshToken("account is gone or disabled")

        # Deleted before the replacement is issued, so a crash between the two
        # ends the session rather than leaving two live handles for it.
        self._tokens.delete(handle)
        return RenewedSession(user=user, refresh_token=self._issue(stored.user_id))

    def end(self, handle: str) -> None:
        """Drop one handle. Logging out on one machine leaves the others alone."""
        self._tokens.delete(handle)

    def end_all(self, user: User) -> None:
        """Drop every handle an account holds, which is what a password change means."""
        assert user.id is not None
        self._tokens.delete_for_user(user.id)

    def _issue(self, user_id: int) -> str:
        handle = secrets.token_urlsafe(TOKEN_BYTES)
        self._tokens.add(RefreshToken(token=handle, user_id=user_id))
        return handle

    def _expired(self, token: RefreshToken) -> bool:
        if token.created_at is None:
            return False
        return datetime.now(timezone.utc) - token.created_at > timedelta(days=self._expires_days)
