# Architecture

How a build gets from a developer's machine onto a board in the field, and what each party checks before it goes any further.

## Three sets of keys

They are easy to confuse and they protect different things.

### TLS certificate

The TLS certificate belongs to the server. It protects the transport and says nothing about whether a firmware image is trustworthy. The device pins it as its CA in `config.json`'s `ca_cert`, because the certificate is self-signed and there is no public CA to chain to.

### Firmware signing

The firmware signing key pair belongs to whoever publishes. The server holds no private key at all. The public half goes in two places, on the account (which is what lets an upload be accepted) and in every device's `config.json` (which is what lets a download be trusted once it lands).

### Device secret

The device secret is 32 bytes of urandom, one per unit. It answers "which device is this" and never authorizes anything. The server stores only a SHA-256 of it.

## The manifest

One string ties the whole system together:

```
model|version|sha256_hex
```

Three parties build it independently and two of them verify it. If any party builds a different string, nothing gets flashed.

```
  developer machine            server                       device
  ----------------             ------                       ------
  build image
  read marker  ------------->  read marker from bytes
  sha256(file) ------------->  sha256(received bytes)
  sign with private key  --->  verify with account's
                               public key
                               store blob as {sha256}.bin
                                      |
                                      |  /api/check
                                      +--------------->  version + signature
                                                         + download_url
                                      |  /api/download
                                      +--------------->  image bytes
                                                         sha256(downloaded)
                                                         rebuild manifest
                                                         verify with the key
                                                         in config.json
                                                         flash
```

The device rebuilds the manifest from its own compiled-in `DEVICE_MODEL`, the hash it computed itself, and the version the server sent. Only that last field comes from the network, and the downgrade check immediately constrains it.

## Build time

`esp32/main/ota.h` carries the two literals that identify an image:

```c
#define FIRMWARE_VERSION "1.0.0"
#define DEVICE_MODEL     "ESP32-S3-N16R8"
```

`ota.cpp` bakes them into a marker that can be found by scanning the binary:

```c
const char FIRMWARE_TAG[] __attribute__((used)) =
    "ESPOTA-BUILD{model=" DEVICE_MODEL ";version=" FIRMWARE_VERSION "}";
```

The marker is what makes "what is this image" a fact readable out of the bytes rather than a label somebody types at upload time. `domain/firmware_image.py` reads it back, and so does the dashboard in the browser.

**THE MARKER SURVIVES ONLY BECAUSE `initOTA()` PRINTS IT.** `__attribute__((used))` stops the compiler dropping an unreferenced constant, but not the linker's `--gc-sections`, which removed the whole string when nothing read it. The sketch came out byte-identical to a build without the marker. Deleting the `Serial` line that prints it silently breaks publishing for every build after it.

## Registration

`POST /api/devices` mints a `device_id` and a `device_secret`, stores a SHA-256 of the secret alongside the owning account and the model, and shows the secret exactly once. Both values go into that unit's `config.json`.

The `model` recorded here is authoritative. A check-in carries a `model` field too, but that is telemetry: the firmware served is the one for the model the unit was registered under.

**The secret is per device, not per tenant.** `config.json` sits in LittleFS in the clear, so buying one unit and dumping its flash yields whatever is in it. Shared across a fleet, that is the firmware for every model the account ships: tearing down a cheap sensor to pull the expensive gateway's image. Per device, a teardown yields future versions for the one unit whose current firmware the attacker already holds.

## Publishing

The dashboard does the whole thing in the browser. `src/crypto/signing.ts` generates the key pair with WebCrypto and signs with it, so publishing needs an account and a browser and nothing else. The CLI scripts do the same job for people who would rather stay in a terminal.

Picking an image runs this, in order: read the build marker and fill the model and version fields read-only, hash the bytes, import the private key as a non-extractable `CryptoKey`, sign the manifest, put the base64 in the signature field.

**The private key input sits outside the `<form>`.** Inside it, the key would stay out of the upload only because the input carries no `name` and `FormData` skips unnamed controls. That is one attribute standing between a signing key and the wire, in a file where every other input exists to be sent. Outside, no arrangement of attributes could send it. `the private key is never sent to the server` in `dashboard.spec.ts` reads every request body to hold this down.

Signing is keyed on what the signature covers rather than on the moment a file is picked. Model and version stay editable for an image with no marker, so a signature computed when the file arrived would be stale by the time it was sent. An effect re-signs whenever any of the four inputs moves.

## What the server checks on upload

`UploadFirmware.execute` runs these in a deliberate order.

**Structure**, via `domain/firmware_image.py`: the 0xE9 header magic, the `esp_app_desc_t` magic at offset 32 (which separates an application image from a bootloader sharing the same header magic), a known chip id at offset 12, a 1 KiB floor, and the trailing SHA-256 when the header says one is appended.

Structural validation is mistake detection, not authenticity. Every field is plain data and `esptool` emits images satisfying all of it. What it catches is the wrong file, a corrupt build, and a truncated download.

**Identity**, via the build marker. The image wins wherever it answers. A typed value that contradicts the marker is refused with both values named rather than quietly corrected, because storing the right thing under an operator who believes they published something else leaves the wrong belief in place for every decision after it. An image with no marker falls back to the typed fields, which is the hole this cannot close, kept so a build from another toolchain can still be published.

**Authenticity**, via the signature. The manifest is rebuilt here out of what the image and the hash say rather than out of anything the form sent: a signature is only worth checking against the values it is about to be stored under. This runs before the blob is written, so a rejection leaves nothing on disk.

A verified upload is stored as `{sha256}.bin`. Content addressing means a name collision implies identical bytes, so the overwrite is harmless and one blob can back several rows.

## What the server checks on check-in

`CheckUpdate.execute`, also in a deliberate order.

Identity first, before anything is read or written. Unknown device, wrong secret, and disabled device all answer the same HTTP 401, so the route cannot be used to discover which device ids exist.

**Then the model, before the firmware lookup.** A body naming a model other than the registered one is refused with a bare 403, the same answer as a model the account has published nothing for.

That ordering is what makes the per-device secret worth anything. Compared after the lookup, "this account publishes that model" and "it does not" would be distinguishable, and one valid secret would read the account's whole model catalogue by trying names. The server log is where the two separate, since it is also the only place an operator can see that a unit was registered under one model and flashed with another.

Then the check-in is recorded, the event log gets a row if the reported version changed, and the latest firmware for the registered model under the owning account is compared with a strict `>`.

## What the device checks before flashing

`OTA()` in `ota.cpp`, after the download has landed in LittleFS:

Hash the downloaded file. Rebuild the manifest from the compiled-in `DEVICE_MODEL`, the offered version, and that hash. Verify against the public key in `config.json`. Refuse anything not strictly newer than `FIRMWARE_VERSION`. Skip the flash when the image is already running, read off the appended digest in the last 32 bytes.

Every path out of `OTA()` removes `/firmware.bin`. The LittleFS partition is 1.25 MB against a roughly 1.1 MB image, so a staged file left behind is nearly all of it.

After a successful flash the device reboots and `markFirmwareValid()` cancels the rollback. The next check-in reports the new version, and the server reads success out of that change.

**A device never reports what it did.** The firmware sends only the version it is running, so `success` and `rollback` are derived in `domain/ota_history.py` from that version changing between two check-ins. `success` therefore means "came up on something newer", not "came up on exactly what was offered".

## Version comparison

Both sides must agree or the two disagree about what is newer.

`signing.parse_version` on the server and `parseVersionSegments`/`isVersionNewer` in `ota.cpp` both read at most three dotted segments and compare as tuples, so `1.2.10 > 1.2.9`. "Latest for a model" is computed in Python, not by SQL ordering, because version strings do not sort lexically.

**`parse_version` stays lenient on purpose.** It mirrors `String::toInt()`, which maps a non-numeric segment to 0 without complaining. Tightening the parser would put the two sides out of step on every deployed device, so the strictness lives on the write side instead: `validate_manifest_fields` holds uploads to exactly three numeric segments.

That guard uses `[0-9]` rather than `\d`. `\d` also matches Unicode decimal digits, `int()` accepts them, and `１.０.０` would parse to `(1, 0, 0)` on the server while the device read `(0, 0, 0)` from the same bytes.

## Download

`GET /api/download/{download_id}?device_id=...` is unauthenticated and has to stay that way, since `ota.cpp` holds no credential to send. Possession of the identifier is the whole of what stands between a caller and the file, which is why the path carries a random 32-character handle rather than the row id. A serial number there would let anyone holding one of their own links derive everyone else's.

The response carries an explicit `Content-Length` taken from the row. A streaming response is otherwise chunked with no length, and `writeToStream` detects a truncated body only by comparing what it copied against that header. The length comes from the row rather than the file so a blob truncated under the row reads as a transport error rather than a shorter promise.

**Attribution is not authorization.** The `device_id` query parameter is never checked against anything, so a caller holding a download id can write a download event against any device id string. It cannot be aimed, since device ids are 12 bytes of urandom, and it reaches only the append-only `device_events`, never anything the dashboard reads as live state.

## Backend layering

Dependencies point inward: `api -> application -> domain`. `infrastructure` implements the ports the inner layers define. Only persistence and storage are abstracted, which is the deliberate boundary that buys a database swap later without abstracting everything.

`domain/` is pure logic with no framework imports. `ports/` is abstract base classes only. `application/` holds one class per use case, constructed with its dependencies. `infrastructure/` holds the concrete adapters, and table rows are distinct from domain dataclasses. `api/` is handlers with no business logic; `deps.py` builds use cases per request and must never import `auth.py`, which depends on it.

## Scoping

`owner_id` on `firmware` and `devices` is the only thing deciding who sees what, and the filter lives on the repository ports as a required argument rather than in the routes. Every read that can return rows takes the account asking, so a route added later cannot forget it: there is nothing to call that does not ask. Forgetting it in a route hands out someone else's data with no test failing.

Two lookups are deliberately unscoped and say so on the port. `get_by_download_id` is unscoped because the identifier is itself the capability, and `DeviceRepository.get_by_device_id` because `/api/check` is how the server finds out whose device is calling.

A row belonging to someone else answers 404, the same as one that never existed. Distinguishing them tells a caller which ids are real.

## Sessions

fastapi-users owns hashing, the login path, the reset token, and the dependency that turns a bearer header into a `User`. It does not own the database: `fastapi-users-db-sqlalchemy` only accepts an `AsyncSession` while everything here is synchronous, so `infrastructure/user_db.py` implements the same `BaseUserDatabase` interface over the existing `UserRepository`.

**fastapi-users has no refresh token.** Rotation is `application/session.py` and the `refresh_tokens` table, written here. A handle is opaque, stored, and single-use, so presenting one both issues a replacement and destroys the original. A copy taken off a machine is usable once and only until the real client renews.

The dashboard holds both credentials in `sessionStorage` and `authFetch` is the only way a page reaches an authenticated endpoint. It renews ahead of expiry, retries once on a 401, and serializes concurrent renewals behind one promise. That last part is not optional: the device page fires three requests at once, and without it each would spend a single-use handle and two would be rejected as replays.

## Known limits

Flash encryption and Secure Boot v2 are not in place, so the device secret and the pinned CA sit in LittleFS in the clear and the bootloader will run an image it cannot vouch for. A leaked signing private key can publish.

Password reset has no mail transport. `on_after_forgot_password` writes the token to the log, which makes a reset an operator-assisted step.

Unreferenced blobs are never swept. Nothing may delete a file because one row went away, since a blob can back several rows, and the job that would collect the orphans does not exist.
