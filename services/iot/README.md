# ESP-Firmware-Over-The-Air

Signed over-the-air firmware updates for ESP32 devices. A FastAPI server stores the builds and answers update checks; the device firmware checks in, downloads, verifies, and flashes.

Whoever builds an image signs it. The server holds no private key and only verifies, so a compromised server cannot produce firmware any device will accept.

<img width="1400" height="868" alt="image" src="https://github.com/user-attachments/assets/cf8aab81-6822-4366-9b6e-edbe789a38d2" />
<img width="1400" height="868" alt="image" src="https://github.com/user-attachments/assets/2de1fd9f-3c9b-4c65-840f-60052655a811" />


## How an update travels

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

All three parties build the same string, `model|version|sha256_hex`, and two of them verify it. If any of them builds a different one, nothing gets flashed.

## Documentation

- [Tutorial](docs/tutorial.md) walks the whole path once, from preparing a sketch to publishing an update and watching it land.
- [Architecture](docs/architecture.md) explains what each party checks and why the checks run in the order they do.
- [Development](docs/development.md) covers running the server and dashboard locally, the test suites, and the migration rules.
- [CONTRIBUTING](CONTRIBUTING.md) covers how work is tracked and how a change gets merged. The plan lives in the repository's GitHub milestones, with the work itself in issues.

## Layout

- `backend/` is the FastAPI server, in a pragmatic Clean Architecture: `api -> application -> domain`, with `infrastructure` implementing the ports.
- `frontend/` is the React dashboard. It generates signing keys and signs uploads in the browser with WebCrypto, so publishing needs an account and a browser and nothing else.
- `esp32/` is the Arduino device firmware. `ota.h` and `ota.cpp` drop into a sketch; `main.ino` is a working example.

## Known limits

- Local development serves HTTPS with a self-signed certificate that the device pins as its CA.
- The device secret sits in LittleFS in the clear, so dumping a unit's flash yields it. Making it genuinely secret needs flash encryption and Secure Boot v2.
- Password reset has no mail transport. The token is written to the server log for an operator to hand over.
- Unreferenced firmware blobs are never swept.
