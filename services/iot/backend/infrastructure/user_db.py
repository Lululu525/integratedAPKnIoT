"""The account store fastapi-users reads through.

fastapi-users talks to a database through `BaseUserDatabase`, and ships an
adapter for SQLAlchemy that only accepts an `AsyncSession`. Everything else
here is synchronous SQLAlchemy, so using that adapter would mean converting
every repository, use case, route and test to async to satisfy one library.
This implements the same interface over the existing `UserRepository` instead,
which keeps the port the rest of the backend already depends on as the single
way accounts are read and written.

The methods are `async` because the library awaits them, not because anything
in them is. They call straight into synchronous SQLite, which blocks the event
loop for the microseconds a local query takes. That is a real constraint on
what this can become: a networked database behind this adapter would need the
calls moved to a thread, and the place to notice is here.
"""

from __future__ import annotations

from typing import Any

from domain.auth import normalize_email
from domain.models import User
from fastapi_users.db import BaseUserDatabase
from ports.repository import UserRepository


class SyncUserDatabase(BaseUserDatabase[User, int]):
    def __init__(self, repository: UserRepository) -> None:
        self._repo = repository

    async def get(self, id: int) -> User | None:
        return self._repo.get_by_id(id)

    async def get_by_email(self, email: str) -> User | None:
        # Normalized on the way in rather than compared case-insensitively in
        # SQL, which is what the library's own adapter does. A `lower(email) =
        # lower(?)` lookup does not match the plain unique index on the column,
        # so two addresses differing only in case would pass the insert and
        # then both be found by one login. Storing one canonical form makes the
        # index itself the thing that refuses the second account.
        return self._repo.get_by_email(normalize_email(email))

    async def create(self, create_dict: dict[str, Any]) -> User:
        create_dict = dict(create_dict)
        create_dict["email"] = normalize_email(create_dict["email"])
        return self._repo.add(User(**create_dict))

    async def update(self, user: User, update_dict: dict[str, Any]) -> User:
        for key, value in update_dict.items():
            setattr(user, key, normalize_email(value) if key == "email" else value)
        return self._repo.update(user)

    async def delete(self, user: User) -> None:
        self._repo.delete(user)
