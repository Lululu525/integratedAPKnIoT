"""Shared local account service for the integrated Apionix portal."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / ".integrated-runtime" / "accounts.db"
SESSION_HOURS = 24
app = FastAPI(title="Apionix Shared Account API")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    email: str = Field(min_length=5, max_length=200)
    password: str = Field(min_length=8, max_length=200)


class LoginRequest(BaseModel):
    username: str
    password: str


class ActivityRequest(BaseModel):
    service: str = Field(pattern=r"^(apk|iot)$")
    action: str = Field(max_length=80)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db() -> None:
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                service TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )


@app.on_event("startup")
def startup() -> None:
    init_db()


def password_hash(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 210_000
    ).hex()


def public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row["email"],
        "created_at": row["created_at"],
    }


def current_user(authorization: str | None = Header(default=None)) -> sqlite3.Row:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Authentication required")
    token = authorization.removeprefix("Bearer ").strip()
    now = datetime.now(timezone.utc).isoformat()
    with connect() as db:
        row = db.execute(
            """
            SELECT users.* FROM sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token = ? AND sessions.expires_at > ?
            """,
            (token, now),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    return row


def create_session(db: sqlite3.Connection, user_id: int) -> str:
    token = secrets.token_urlsafe(36)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat()
    db.execute(
        "INSERT INTO sessions(token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires_at),
    )
    return token


@app.post("/auth/register")
def register(payload: Credentials) -> dict:
    if "@" not in payload.email:
        raise HTTPException(status_code=422, detail="請輸入有效的電子信箱")
    salt = os.urandom(16).hex()
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        with connect() as db:
            cursor = db.execute(
                """
                INSERT INTO users(username, email, password_hash, salt, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    payload.username.strip(),
                    payload.email.strip(),
                    password_hash(payload.password, salt),
                    salt,
                    created_at,
                ),
            )
            token = create_session(db, cursor.lastrowid)
            row = db.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="使用者名稱或電子信箱已被使用")
    return {"token": token, "user": public_user(row)}


@app.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    with connect() as db:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (payload.username.strip(),),
        ).fetchone()
        if not row or not hmac.compare_digest(
            password_hash(payload.password, row["salt"]), row["password_hash"]
        ):
            raise HTTPException(status_code=401, detail="使用者名稱或密碼錯誤")
        token = create_session(db, row["id"])
    return {"token": token, "user": public_user(row)}


@app.get("/auth/me")
def me(user: sqlite3.Row = Depends(current_user)) -> dict:
    return {"user": public_user(user)}


@app.post("/auth/logout")
def logout(
    authorization: str | None = Header(default=None),
    user: sqlite3.Row = Depends(current_user),
) -> dict:
    del user
    token = authorization.removeprefix("Bearer ").strip()
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    return {"ok": True}


@app.post("/activity")
def add_activity(
    payload: ActivityRequest, user: sqlite3.Row = Depends(current_user)
) -> dict:
    with connect() as db:
        db.execute(
            "INSERT INTO activity(user_id, service, action, created_at) VALUES (?, ?, ?, ?)",
            (
                user["id"],
                payload.service,
                payload.action,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
    return {"ok": True}


@app.get("/activity")
def list_activity(user: sqlite3.Row = Depends(current_user)) -> dict:
    with connect() as db:
        rows = db.execute(
            """
            SELECT service, action, created_at FROM activity
            WHERE user_id = ? ORDER BY id DESC LIMIT 50
            """,
            (user["id"],),
        ).fetchall()
    return {"items": [dict(row) for row in rows]}
