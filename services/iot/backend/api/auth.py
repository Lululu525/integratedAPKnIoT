"""Account management and the session routes built on top of it.

fastapi-users owns everything about an account that is the same in every
application: hashing (pwdlib picks argon2, and verifies the bcrypt hashes
written before it), the constant-time login path with its hash upgrade, the
signed reset-password token, and the dependencies that turn a bearer header
into a `User`. `UserManager` below is where this project's own rules attach to
that machinery, and it is the single door: `scripts/create_user.py` calls
`create()` on it exactly as the HTTP routes do, so the script cannot be the
more permissive of the two.

Login, logout and refresh are written here rather than taken from
`get_auth_router`, because each of them has to carry the refresh handle and the
library's router has nowhere to put it. The part of login worth not
hand-rolling is `UserManager.authenticate`, and that is what these call.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from application.session import InvalidRefreshToken, RenewedSession, Session
from config import Settings, get_settings
from domain import auth
from domain.models import User
from domain.signing import InvalidPublicKey, load_public_key
from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin, schemas
from fastapi_users import exceptions as fu_exceptions
from fastapi_users.authentication import AuthenticationBackend, BearerTransport, JWTStrategy
from fastapi_users.db import BaseUserDatabase
from pydantic import BaseModel

from api.deps import get_session, get_user_db

logger = logging.getLogger(__name__)


class UserCreate(schemas.BaseUserCreate):
    """What `UserManager.create` is handed. `EmailStr` is the whole of the
    address validation, per the identity migration; the password rules are
    `domain.auth`'s and run in `validate_password` below.

    `is_superuser` is on the base schema and is dropped by `create_update_dict`
    on the registration path, so signing up cannot grant it. Nothing reads it
    either way: an account reaches what it owns.
    """


class UserRead(schemas.BaseUser[int]):
    """What the register route echoes back."""


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    """Account rules, and the hooks the library calls around them."""

    def __init__(
        self,
        user_db: BaseUserDatabase[User, int],
        session: Session,
        reset_secret: str,
    ) -> None:
        super().__init__(user_db)
        self._session = session
        self.reset_password_token_secret = reset_secret
        # Set because the base class declares it, not because anything issues
        # one. Email verification needs a mail transport this server does not
        # have, and no route gates on `is_verified`.
        self.verification_token_secret = reset_secret

    async def validate_password(self, password: str, user: User) -> None:
        try:
            auth.validate_password(password)
        except auth.InvalidCredentialFormat as exc:
            raise fu_exceptions.InvalidPasswordException(reason=str(exc)) from exc

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("Account registered: %s (id=%s)", user.email, user.id)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        # The token goes to the log because there is no mail transport here.
        # That makes a reset an operator-assisted flow: someone with server
        # access reads the token out and hands it over. The routes, the token
        # and its expiry are the library's either way, so wiring SMTP later
        # changes this method and nothing else.
        logger.warning(
            "Password reset requested for %s. No mail transport is configured, "
            "so the token is only readable here: %s",
            user.email,
            token,
        )

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        # Every handle dies with the old password. Leaving them live would mean
        # a reset does not end the session the person resetting was locked out
        # of, which is the case a reset most often exists for.
        self._session.end_all(user)
        logger.info("Password reset for %s; all sessions ended", user.email)


async def get_user_manager(
    user_db: BaseUserDatabase[User, int] = Depends(get_user_db),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> AsyncIterator[UserManager]:
    yield UserManager(user_db, session, settings.jwt_secret)


def get_jwt_strategy(settings: Settings = Depends(get_settings)) -> JWTStrategy[User, int]:
    return JWTStrategy(
        secret=settings.jwt_secret,
        lifetime_seconds=settings.jwt_expires_minutes * 60,
    )


auth_backend = AuthenticationBackend(
    name="jwt",
    transport=BearerTransport(tokenUrl="api/auth/login"),
    get_strategy=get_jwt_strategy,
)

fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])

# The one shape every protected route reaches for. There is no privileged
# variant: what an account may do is decided by what it owns, and every
# repository takes the account asking. `active=True` is what makes a disabled
# account stop working on its next request rather than at the end of its
# access token.
current_active_user = fastapi_users.current_user(active=True)


class AccountResponse(BaseModel):
    """Who is signed in, plus whether this account can publish at all.

    The key itself is public, but the dashboard only needs to know it is there:
    the upload form is unusable without one, and saying so before the operator
    fills it in is the whole reason this field exists.
    """

    id: int
    email: str
    has_public_key: bool = False


class SessionResponse(BaseModel):
    """What a login or a refresh hands back.

    `expires_in` is stated rather than left to be read out of the JWT. The
    dashboard needs to know when to renew, and decoding a token to find out
    means the browser parsing a credential it is only supposed to carry.

    The account travels with it so the dashboard has a name to show without a
    second round trip, which is one fewer state it can be caught between.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str
    user: AccountResponse


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _session_response(
    user: User, access_token: str, refresh_token: str, settings: Settings
) -> SessionResponse:
    assert user.id is not None
    return SessionResponse(
        access_token=access_token,
        expires_in=settings.jwt_expires_minutes * 60,
        refresh_token=refresh_token,
        user=AccountResponse(id=user.id, email=user.email, has_public_key=bool(user.public_key)),
    )


@router.post("/login")
async def login(
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager: UserManager = Depends(get_user_manager),
    strategy: JWTStrategy[User, int] = Depends(get_jwt_strategy),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    # `OAuth2PasswordRequestForm` calls the identity field `username`. It is an
    # email; the form field name is OAuth2's and renaming it would break every
    # generated client that knows the standard shape.
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = await strategy.write_token(user)
    return _session_response(user, access_token, session.start(user), settings)


@router.post("/refresh")
async def refresh(
    refresh_token: str = Body(..., embed=True),
    strategy: JWTStrategy[User, int] = Depends(get_jwt_strategy),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> SessionResponse:
    # Deliberately not behind the bearer dependency. The whole point of this
    # route is to be reachable once the access token has expired, so requiring
    # a live one would make it useful only while it is not needed.
    try:
        renewed: RenewedSession = session.renew(refresh_token)
    except InvalidRefreshToken as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    access_token = await strategy.write_token(renewed.user)
    return _session_response(renewed.user, access_token, renewed.refresh_token, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    refresh_token: str = Body(..., embed=True),
    session: Session = Depends(get_session),
) -> None:
    # Unauthenticated for the same reason as refresh, and harmless: holding the
    # handle is the only thing being asked for, and the effect is to destroy it.
    # The access token is stateless and outlives this by design; the dashboard
    # drops it, and it expires on its own.
    session.end(refresh_token)


@router.get("/me")
async def read_me(user: User = Depends(current_active_user)) -> AccountResponse:
    assert user.id is not None
    return AccountResponse(id=user.id, email=user.email, has_public_key=bool(user.public_key))


class PublicKeyRequest(BaseModel):
    public_key: str


@router.put("/public-key")
async def set_public_key(
    body: PublicKeyRequest,
    user: User = Depends(current_active_user),
    user_manager: UserManager = Depends(get_user_manager),
) -> AccountResponse:
    """Set the key this account's uploads are verified against.

    A route rather than a setup step, because registration is open: an account
    created in the browser would otherwise have no way to ever publish, and
    telling a new tenant to go find shell access on the server is not a signup
    flow.

    Replacing the key does not touch what is already published. Those rows hold
    signatures made under the old key and devices still verify them against the
    copy in their own `config.json`, so rotating here without reflashing the
    fleet changes nothing for anything already in the field.
    """
    try:
        normalized = load_public_key(body.public_key)
    except InvalidPublicKey as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    updated = await user_manager.user_db.update(user, {"public_key": normalized})
    assert updated.id is not None
    return AccountResponse(
        id=updated.id, email=updated.email, has_public_key=bool(updated.public_key)
    )


def build_auth_router() -> APIRouter:
    """This module's routes plus the library's registration and reset pairs.

    Signup is open. It was closed while every account shared one firmware list,
    where a new account meant a stranger reading and publishing alongside
    everyone else. With firmware and devices scoped to their owner, a new
    account arrives at an empty list of its own and can reach nothing else.
    """
    combined = APIRouter()
    combined.include_router(router)
    combined.include_router(
        fastapi_users.get_register_router(UserRead, UserCreate),
        prefix="/api/auth",
        tags=["auth"],
    )
    combined.include_router(
        fastapi_users.get_reset_password_router(), prefix="/api/auth", tags=["auth"]
    )
    return combined


__all__ = [
    "AccountResponse",
    "UserCreate",
    "UserManager",
    "UserRead",
    "build_auth_router",
    "current_active_user",
    "fastapi_users",
    "get_user_manager",
]
