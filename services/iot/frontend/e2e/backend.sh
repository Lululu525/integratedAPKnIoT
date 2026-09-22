#!/usr/bin/env bash
# The backend the Playwright run drives: its own database, its own key pair and
# one seeded account, none of it shared with the dev server under backend/data.
# Wiped on every start, so a rerun cannot inherit the rows an earlier run
# uploaded and read a 409 where it expects a fresh publish.
#
# The key pair belongs to the uploader, not to the server. The public half goes
# on the seeded account, and the spec signs with the private half out of
# $KEYS_DIR, which is what makes the upload flow reachable from a browser test
# at all: the server signs nothing and refuses an upload it cannot verify.
#
# Plain HTTP on the loopback. The TLS cert under backend/keys is issued for a
# LAN IP so the device can pin it, and neither CI nor this test has one.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(cd "$here/../.." && pwd)"
scratch="$here/.tmp"

rm -rf "$scratch"

export DATA_DIR="$scratch/data"
export KEYS_DIR="$scratch/keys"
# Throwaway values for a server that holds nothing but test uploads. 32 bytes
# is the floor config enforces on the JWT key.
export JWT_SECRET="e2e-secret-not-for-production-padded"
export OTA_USER_PASSWORD="e2e-password"

mkdir -p "$DATA_DIR" "$KEYS_DIR"

cd "$root"
uv run python backend/scripts/generate_keys.py
uv run alembic -c backend/alembic.ini upgrade head
# Seeded rather than registered through the route, so the suite has an account
# before the first page load. Not an @e2e.test address: `EmailStr` runs
# email-validator, which refuses the special-use TLDs reserved by RFC 2606, and
# the seeding step then fails before the server starts with nothing in the
# Playwright output but a webServer timeout. example.com is a real registered
# domain and passes.
uv run python backend/scripts/create_user.py --email admin@example.com \
    --public-key "$KEYS_DIR/public_key.pem"

exec uv run uvicorn main:app --app-dir backend --host 127.0.0.1 --port "${E2E_BACKEND_PORT:-8100}"
