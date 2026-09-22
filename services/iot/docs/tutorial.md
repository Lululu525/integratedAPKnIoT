# Tutorial

From nothing to a board that updates itself. This walks the whole path once: prepare a sketch, register the unit, flash it, then publish a new version and watch it arrive.

It assumes a running server and dashboard. See [development.md](development.md) if you still need to bring those up.

## Requirement

An ESP32-S3 with at least 4 MB of flash, `arduino-cli` with the `esp32:esp32` core installed, and an account on the dashboard.

The partition table in `esp32/main/partitions.csv` splits the flash into two 1.25 MB app slots (OTA needs a spare one to write into) and a 1.25 MB LittleFS partition for `config.json`. A board with less flash will not fit it.

## Prepare the sketch

Copy `ota.h` and `ota.cpp` next to your own sketch. `esp32/main/main.ino` is a working example of how they fit together: initialise LittleFS, load the config, call `initOTA()`, connect WiFi, sync the clock, then call `check()` on a timer and hand a successful offer to `downloadFirmwareToFS()` and `OTA()`.

`syncTimeSNTP()` is not optional. TLS certificate validation compares the certificate's validity window against the device clock, which starts at the epoch, so every handshake fails until the clock is real.

At the top of `ota.h`, set what this build is:

```c
#define FIRMWARE_VERSION "1.0.0"
#define DEVICE_MODEL     "ESP32-S3-N16R8"
```

`DEVICE_MODEL` is a free-form label. Whatever you put here is what you register the unit as, and the two must match exactly.

`FIRMWARE_VERSION` must be three numeric segments. A `v` prefix or a fourth segment parses to something you did not mean, and the upload is refused for exactly this reason.

### Do not delete the line that prints the build tag

`ota.cpp` builds a marker out of those two literals:

```c
const char FIRMWARE_TAG[] __attribute__((used)) =
    "ESPOTA-BUILD{model=" DEVICE_MODEL ";version=" FIRMWARE_VERSION "}";
```

The server finds this marker in the uploaded bytes and publishes the image under what it says, so the stored label cannot drift from what the device will report.

`__attribute__((used))` is not enough to keep it. It stops the compiler dropping an unreferenced constant, but the linker's `--gc-sections` collects the whole string anyway when nothing reads it. `initOTA()` prints the tag, and that print is the only reason it survives. Remove it and your builds compile fine, carry no marker, and quietly fall back to whatever model and version somebody types into the upload form.

## Set a signing key on your account

The server verifies uploads and never signs them, so an account with no public key cannot publish anything.

On the firmware screen (`韌體管理`), the signing key card (`簽章公鑰`) generates a key pair in your browser. The private half downloads as a file and is shown once; the public half is filled in for you. Keep the private key somewhere you will still have it next release.

To generate the pair in a shell instead:

```bash
uv run python backend/scripts/generate_keys.py
```

Then paste the contents of `backend/keys/public_key.pem` into the same card. Either pair works with either signing path; WebCrypto reads the PKCS#8 the script writes.

## Register the device

On the device screen (`裝置監控`), use `註冊裝置` and enter the same string you put in `DEVICE_MODEL`. It answers with:

```json
{
  "device_id": "uWoSZJzfz3RxVgSe",
  "device_secret": "ypflIBgP_i6rm3HbmBAiyY0lC-ttgTjkW42f9XPG3Dc"
}
```

The secret is shown exactly once. The server keeps only a SHA-256 of it. Lose it and the unit has to be registered again.

Both values are per device. Ten boards means ten registrations and ten different `config.json` files.

## Write config.json

Copy the template and fill it in:

```bash
cp data/config.json.example data/config.json
```

| Field                                            | Where it comes from                                          |
| ------------------------------------------------ | ------------------------------------------------------------ |
| `wifi_ssid`, `wifi_password`                     | your network                                                 |
| `use_enterprise`, `eap_identity`, `eap_username` | WPA2 Enterprise only, otherwise leave as is                  |
| `server_url`                                     | the address the board dials, must match the certificate's IP |
| `device_id`, `device_secret`                     | the registration above                                       |
| `ca_cert`                                        | `backend/keys/tls_cert.pem`                                  |
| `public_key`                                     | the public half of your signing key                          |

Both PEM values are JSON strings, so the newlines have to be written as `\n`.

`data/config.json` is git-ignored. It carries a WiFi password and that unit's secret in the clear.

## Flash the board

Build the LittleFS image and write it to the filesystem partition:

```bash
~/.arduino15/packages/esp32/tools/mklittlefs/4.0.2-db0513a/mklittlefs \
  -c data -p 256 -b 4096 -s 1310720 spiffs.bin

~/.arduino15/packages/esp32/tools/esptool_py/5.3.0/esptool \
  --chip esp32s3 --port /dev/ttyACM0 --baud 921600 \
  write_flash 0x2b0000 spiffs.bin
```

`0x2b0000` and `1310720` come from `partitions.csv`. Change the table and both numbers change with it.

Then flash the application:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3 \
  --board-options "PartitionScheme=custom,CDCOnBoot=cdc" \
  --upload --port /dev/ttyACM0 esp32/main
```

On the serial console you should see the config load, WiFi connect, the clock sync, and then a check-in every few seconds. The device appears on `裝置監控` after the first one.

## Publish an update

Bump the version in `ota.h`:

```c
#define FIRMWARE_VERSION "1.0.1"
```

Build, and export the binary:

```bash
arduino-cli compile --fqbn esp32:esp32:esp32s3 \
  --board-options "PartitionScheme=custom,CDCOnBoot=cdc" \
  --export-binaries esp32/main
```

`--export-binaries` is not optional. Without it `arduino-cli` writes to a temp directory and leaves `esp32/main/build/` untouched, so reading `main.ino.bin` out of there hands you a build from some earlier run. The compile reports the new sizes, the file keeps its old timestamp, and the bytes you publish are not the bytes you just built. Check the mtime before uploading.

Then on `發布韌體`, pick the `.bin`. The model and version fill themselves in from the build marker and become read-only. Pick your private key file and the signature is computed in the browser.

To sign in a shell instead:

```bash
uv run python backend/scripts/sign_firmware.py \
  esp32/main/build/esp32.esp32.esp32s3/main.ino.bin
```

Paste what it prints into the signature field. Either way the private key stays on your machine; the upload carries the image and the signature and nothing else.

The device picks it up on its next check-in. On serial you get the manifest, the signature result, the byte count written, and a reboot into the new version.

### Publishing from a script

```python
import requests

base = 'https://YOUR_SERVER_IP:8000'
token = requests.post(
    f'{base}/api/auth/login',
    data={'username': 'you@example.com', 'password': '...'},
).json()['access_token']

image = 'esp32/main/build/esp32.esp32.esp32s3/main.ino.bin'
requests.post(
    f'{base}/firmware/upload',
    files={'firmware': ('main.ino.bin', open(image, 'rb'))},
    data={'signature': 'PASTE_THE_SIGNATURE'},
    headers={'Authorization': f'Bearer {token}'},
)
```

`model` and `version` are optional form fields. The server reads them out of the image and refuses a typed value that contradicts the marker, so send them only for an image that carries none.

## When it does not work

**Nothing appears on the device page and serial shows no HTTP error at all.** The TLS handshake never completed, so the device never sent a request. Either `server_url` does not match the certificate, the certificate in `ca_cert` is not the one the server is using, or the clock is wrong.

**HTTP 401 on check-in.** The handshake and the request were both fine. The unit is not registered, has the wrong secret, or was disabled from the dashboard.

**HTTP 403 on check-in.** Everything is fine and the account simply has no firmware for that model, or the `DEVICE_MODEL` in the build disagrees with what the unit was registered as. The server log distinguishes these two; the response deliberately does not.

**The signature verifies on the server and fails on the device.** The `public_key` in `config.json` is not the public half of the key that signed. Rotating an account's key does not reach backwards, and it does not reach the fleet at all.

**The device flashes, reboots, and is offered the same update again.** The version the image reports disagrees with the version it was published under. This only happens for an image with no build marker, published under typed values.

### After the LAN IP changes

A new network changes the dev machine's address, and three things go stale together: the certificate's SAN, `server_url` and `ca_cert` in `config.json`, and `VITE_BACKEND` in `frontend/.env.local`. Regenerate the certificate for the new address, update `config.json`, and rewrite just the LittleFS partition. The application does not need recompiling.

**Regenerating the certificate for the same IP breaks it the same way with none of the symptoms.** The new certificate carries a fresh key, so the copy in `config.json` no longer matches it, while the CN and `server_url` still read correct and nothing compares them. Diff the serials:

```bash
openssl x509 -in backend/keys/tls_cert.pem -noout -serial

python -c "import json;print(json.load(open('data/config.json'))['ca_cert'])" \
  | openssl x509 -noout -serial
```
