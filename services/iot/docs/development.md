# Development

Running the server, the dashboard and the tests locally.

Python is managed exclusively with `uv`. Never call `pip` or a bare `python`.

## First run

```bash
uv sync                                  # add --group dev for the linters

# The key pair you sign firmware with. It belongs to whoever publishes, not to
# the server: the server holds no private key and only verifies. The dashboard
# can generate the same pair in a browser, so this is for people who prefer a
# shell.
uv run python backend/scripts/generate_keys.py

# A self-signed TLS cert for your LAN IP. The device pins it as its CA, so the
# IP has to match server_url in config.json.
uv run python backend/scripts/generate_tls_cert.py <your-lan-ip>

uv run alembic -c backend/alembic.ini upgrade head

# JWT_SECRET has no default and needs 32+ bytes. The server refuses to boot
# without it; scripts and alembic run fine before it exists.
cp backend/.env.example backend/.env

# Seed an account. Signup is open, so this is for a fresh install, CI, and
# frontend/e2e/backend.sh.
uv run python backend/scripts/create_user.py --email you@example.com \
  --public-key backend/keys/public_key.pem
```

The SQLite database and the firmware blobs live under `backend/data/`, the signing pair and the TLS cert under `backend/keys/`. Both are git-ignored, as is `backend/.env`.

There is no CLI for registering a device. It is a dashboard action, because the secret it prints is shown exactly once.

## Run

```bash
uv run uvicorn main:app --app-dir backend --host 0.0.0.0 --reload \
  --ssl-keyfile backend/keys/tls_key.pem \
  --ssl-certfile backend/keys/tls_cert.pem
```

`--app-dir backend` matters: imports are top-level, as in `from api.routes`. `--host 0.0.0.0` is what makes the server reachable from a device on the LAN. The SSL flags are required, since the device dials an `https://` URL.

```bash
cd frontend
npm install
cp .env.example .env.local   # VITE_BACKEND points at the server above
npm run dev
```

The dev server proxies `/backend/*` to `VITE_BACKEND`, so the backend has to be up and reachable at that URL already.

## Tests and linters

```bash
uv run pytest                        # backend unit tests
uv run pre-commit run --all-files    # black, ruff, clang-format, editorconfig
npm run test:e2e --prefix frontend   # Playwright, starts both servers itself
```

CI runs the same three on every pull request. `black` and `ruff` are configured for line length 100.

Backend coverage spans `domain/signing.py`, the `application/` use cases against fake ports, `sqlite_repo.py` and `local_storage.py` against a real in-memory database and a tmp dir, and `api/routes.py` through FastAPI's `TestClient`. `test_scoping.py` and `test_device_registration.py` drive the real routes to check that one account cannot reach another's firmware or devices. `test_publishing.py` runs the whole verify-then-store path with real keys. The C++ side has no test coverage.

**`editorconfig-checker` runs with `pass_filenames: false`.** It scans the whole repo on every commit while pre-commit has stashed the unstaged changes, so staging part of the work checks a tree that never existed and violations in files you left unstaged fail a commit that does not touch them. Either stage everything or pass `--no-verify` on intermediate commits and let the last one gate the final tree.

## The e2e suite

`playwright.config.ts` starts both servers. `e2e/backend.sh` builds a throwaway backend under `frontend/e2e/.tmp` with its own `DATA_DIR`, `KEYS_DIR`, `JWT_SECRET` and one seeded account, wiping it on every start so a rerun cannot read a 409 where it expects a fresh publish. It serves plain HTTP on the loopback, since the cert under `backend/keys/` is issued for a LAN IP no CI runner has.

The vite server runs with `--mode e2e`, which is what makes `frontend/.env.e2e` win over a developer's git-ignored `.env.local`.

The spec signs with `node:crypto` out of that backend's `KEYS_DIR`, whose public half `backend.sh` seeds onto the account. The server signs nothing, so without a real RSA-PSS signature there is no upload for a browser test to make.

**Both servers are pinned to `127.0.0.1`.** Vite binds one address rather than both families, and its default `localhost` is whatever the resolver puts first: 127.0.0.1 on a machine with an IPv4-only hosts file, `::1` on a runner whose hosts file carries both. Bound to `::1` it never answers the 127.0.0.1 the tests poll, and the only symptom is a bare `webServer` timeout. Both entries also set `stdout: 'pipe'`, without which a server that fails to start reports a timeout naming neither of them.

**Seeded accounts need a real domain.** `EmailStr` runs email-validator, which refuses the special-use TLDs RFC 2606 reserves, so `admin@e2e.test` fails the seeding step and Playwright reports only a webServer timeout. `example.com` passes.

Each test invents its own model name, so the cases share one database without sharing state. With a fixed name the duplicate case would depend on the publish case having run first, and a retry would re-run a publish into the 409 it is not testing.

## Migrations

Schema changes go through Alembic in `backend/alembic/versions/`. The head is `0014_account_public_key`.

**A migration that validates anything has to do it before its first `ALTER`.** Alembic runs SQLite with non-transactional DDL, so a raise partway through leaves the added columns on the table while `alembic_version` stays on the old revision. The retry then dies on `duplicate column name` and names nothing an operator can act on. `0006` reads the whole firmware table and checks every blob exists before adding a column, for exactly this reason.

Two migrations deliberately refuse to run rather than repair. `0010` will not run on a database that already violates the unique binary rule: a `(model, version)` duplicate was unservable either way, while two versions sharing one binary are both servable and choosing which disappears is not a migration's call. `0013` mints no secret for existing device rows, because a secret is only useful once it is in a unit's LittleFS image and inventing one would make an unusable value look like a working registration.

## ESP32 builds

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3 \
  --board-options "PartitionScheme=custom,CDCOnBoot=cdc" \
  --export-binaries esp32/main
```

**Always pass `--export-binaries`.** Without it the output goes to a temp directory and `esp32/main/build/` keeps whatever an earlier run left there, so the bytes you publish are not the bytes you just built. Check the mtime before flashing.

`esp32/scripts/gen_compile_commands.sh` fills `esp32/.cdb/` with a compile database for clangd. Without it every Arduino include fails to resolve and the whole file reports as errors. `esp32/.clangd` strips the four GCC-only flags clang rejects and tells clangd that `.ino` is C++ with `Arduino.h` implied.

## The device's config

`data/config.json` is one unit's LittleFS contents: its WiFi credentials, the server URL it dials, its `device_id` and `device_secret`, the CA it pins, and the public key it verifies firmware against. Copy `data/config.json.example` and fill it in. It is git-ignored, and it carries a WiFi password and a device secret in the clear.

**The device pins a certificate, not an address.** `ca_cert` holds the bytes of `backend/keys/tls_cert.pem` as they were when the image was built. Regenerating that certificate breaks the pin even when the IP has not changed, and the board reports only `http connect error: -1`, the same thing it says for a server that is down. Compare the two before looking anywhere else:

```bash
openssl x509 -in backend/keys/tls_cert.pem -noout -fingerprint -sha256
uv run python -c "import json,pathlib; print(json.loads(pathlib.Path('data/config.json').read_text())['ca_cert'])" \
  | openssl x509 -noout -fingerprint -sha256
```

`server_url` has to name an IP in the certificate's SAN, so changing networks means reissuing the certificate with `generate_tls_cert.py` and then rebuilding the filesystem image below.

## Flashing the filesystem

`config.json` reaches the board as a LittleFS image written to the partition `esp32/main/partitions.csv` calls `spiffs`, at offset `0x2b0000` and `0x140000` long. Uploading a sketch does not touch it, which is why a stale pinned certificate survives every reflash of the firmware.

```bash
MKLITTLEFS=$(find ~/.arduino15/packages/esp32/tools/mklittlefs -type f -name mklittlefs | head -1)
"$MKLITTLEFS" -c data -b 4096 -p 256 -s 0x140000 /tmp/littlefs.bin

~/.arduino15/packages/esp32/tools/esptool_py/*/esptool --chip esp32s3 --port /dev/ttyACM0 \
  --baud 921600 write-flash 0x2b0000 /tmp/littlefs.bin
```

The whole `data/` directory goes in, so `config.json.example` rides along. It is half a kilobyte against a 1.25 MB partition and nothing reads it.

## Reading the serial log

```bash
stty -F /dev/ttyACM0 115200 raw -echo -hupcl
cat /dev/ttyACM0
```

`arduino-cli monitor` comes back empty against this board's USB CDC. `cat` on the device node works.

The board prints its check-in body on every poll, with `device_secret` replaced by `<redacted>`. Flash can be dumped by anyone holding the board, so serial adds no capability to them, but a serial log is a thing people paste into an issue and a flash dump is not. A build from before that change prints the real secret several times a minute, so check what the board is running before capturing a log you intend to share.

## Frontend conventions

There is no Prettier and there are no stylistic ESLint rules. The 2-space convention is enforced only by `.editorconfig`, which is inert in VS Code without the EditorConfig extension. If a contributor's saves keep reformatting to 4 spaces, check that before suspecting anything else. JetBrains IDEs and vim with an editorconfig plugin honor it natively.
