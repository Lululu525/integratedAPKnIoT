"""Start and supervise the complete local Apionix demo stack."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
APK_ROOT = WORKSPACE / "apk-analysis-platform"
EMBEDDED_IOT_ROOT = ROOT / "services" / "iot"
IOT_ROOT = (
    EMBEDDED_IOT_ROOT
    if (EMBEDDED_IOT_ROOT.exists())
    else WORKSPACE / "ESP-Firmware-Over-The-Air"
)
APK_PYTHON = APK_ROOT / ".venv" / "Scripts" / "python.exe"
IOT_PYTHON = IOT_ROOT / ".venv" / "Scripts" / "python.exe"
DEMO_EMAIL = "demo@apionix.example"
DEMO_PASSWORD = "apionix-local-demo-2026"
RUNTIME_ROOT = ROOT / ".integrated-runtime"
APK_FRONTEND_RUNTIME = RUNTIME_ROOT / "apk-frontend"
IOT_FRONTEND_RUNTIME = RUNTIME_ROOT / "iot-frontend"


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_http(url: str, timeout: float = 45.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def run_checked(command: list[str], cwd: Path, env=None) -> None:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=env or os.environ.copy(),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = f"{result.stdout}\n{result.stderr}".strip()
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{output}")


def ensure_iot_runtime(iot_env: dict[str, str]) -> None:
    """Install and build the bundled upstream IoT application when needed."""
    Path(iot_env["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
    if not IOT_PYTHON.exists():
        uv = shutil.which("uv")
        if not uv:
            raise RuntimeError("uv is required to install the bundled IoT service.")
        print("[setup] Installing bundled IoT backend dependencies")
        run_checked([uv, "sync", "--frozen", "--no-dev"], IOT_ROOT, iot_env)

    frontend_dist = IOT_ROOT / "frontend" / "dist" / "index.html"
    if not frontend_dist.exists():
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise RuntimeError("Node.js and npm are required to build the IoT dashboard.")
        print("[setup] Installing bundled IoT frontend dependencies")
        run_checked([npm, "ci"], IOT_ROOT / "frontend", iot_env)
        print("[setup] Building bundled IoT dashboard")
        run_checked([npm, "run", "build"], IOT_ROOT / "frontend", iot_env)

    print("[setup] Applying bundled IoT database migrations")
    run_checked(
        [str(IOT_PYTHON), "-m", "alembic", "-c", "backend/alembic.ini", "upgrade", "head"],
        IOT_ROOT,
        iot_env,
    )

    public_key = Path(iot_env["KEYS_DIR"]) / "public_key.pem"
    if not public_key.exists():
        print("[setup] Generating the local IoT demo signing key pair")
        run_checked(
            [str(IOT_PYTHON), "backend/scripts/generate_keys.py"],
            IOT_ROOT,
            iot_env,
        )


def ensure_iot_demo_user(iot_env: dict[str, str]) -> None:
    user_env = iot_env.copy()
    user_env["OTA_USER_PASSWORD"] = DEMO_PASSWORD
    result = subprocess.run(
        [
            str(IOT_PYTHON),
            "backend/scripts/create_user.py",
            "--email",
            DEMO_EMAIL,
            "--public-key",
            str(Path(iot_env["KEYS_DIR"]) / "public_key.pem"),
        ],
        cwd=IOT_ROOT,
        env=user_env,
        capture_output=True,
        text=True,
    )
    output = f"{result.stdout}\n{result.stderr}"
    if result.returncode != 0 and "already exists" not in output:
        raise RuntimeError(f"Could not create the local IoT demo account:\n{output}")


def fetch_iot_demo_session() -> dict[str, object]:
    form = urllib.parse.urlencode(
        {"username": DEMO_EMAIL, "password": DEMO_PASSWORD}
    ).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8100/api/auth/login",
        data=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        body = json.load(response)
    return {
        "accessToken": body["access_token"],
        "refreshToken": body["refresh_token"],
        "isGuest": True,
        "expiresAt": int(time.time() * 1000) + int(body["expires_in"]) * 1000,
        "account": {
            "id": body["user"]["id"],
            "email": body["user"]["email"],
            "hasPublicKey": bool(body["user"].get("has_public_key")),
        },
    }


def prepare_apk_frontend() -> None:
    """Create an integration-only APK build without its duplicate portal header."""
    source = APK_ROOT / "FrontendUI" / "dist"
    if APK_FRONTEND_RUNTIME.exists():
        shutil.rmtree(APK_FRONTEND_RUNTIME)
    APK_FRONTEND_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, APK_FRONTEND_RUNTIME)

    index_path = APK_FRONTEND_RUNTIME / "index.html"
    html = index_path.read_text(encoding="utf-8")
    integration_styles = (
        "<style id=\"apionix-integration-apk\">"
        "#root>div>header:first-child{display:none!important}"
        "</style>"
    )
    index_path.write_text(
        html.replace("</head>", f"{integration_styles}\n</head>", 1),
        encoding="utf-8",
    )


def prepare_iot_frontend(session: dict[str, object]) -> None:
    source = IOT_ROOT / "frontend" / "dist"
    if IOT_FRONTEND_RUNTIME.exists():
        shutil.rmtree(IOT_FRONTEND_RUNTIME)
    IOT_FRONTEND_RUNTIME.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, IOT_FRONTEND_RUNTIME)

    index_path = IOT_FRONTEND_RUNTIME / "index.html"
    html = index_path.read_text(encoding="utf-8")
    stored_session = json.dumps(session, ensure_ascii=False, separators=(",", ":"))
    bootstrap = (
        "<meta name=\"apionix-local-demo\" content=\"enabled\">\n"
        "<script>"
        "if (window.top === window.self) {"
        "var portal = location.protocol + '//' + location.hostname + ':8080/iot-system.html';"
        "location.replace(portal);"
        "}"
        "sessionStorage.setItem('ota.session', "
        f"{json.dumps(stored_session)});"
        "function fixApionixBackLink() {"
        "var back = document.querySelector('.header-back-link');"
        "if (!back || back.dataset.apionixFixed === 'ready') return;"
        "back.dataset.apionixFixed = 'ready';"
        "back.href = 'http://127.0.0.1:8080/';"
        "back.onclick = function(event) { event.preventDefault(); window.top.location.href = 'http://127.0.0.1:8080/'; };"
        "}"
        "new MutationObserver(fixApionixBackLink).observe(document.documentElement, {childList:true, subtree:true});"
        "addEventListener('DOMContentLoaded', fixApionixBackLink);"
        "</script>"
    )
    guest_styles = (
        "<style>"
        ".header-auth{display:none!important}"
        "</style>"
    )
    index_path.write_text(
        html.replace("</head>", f"{guest_styles}\n{bootstrap}\n</head>", 1),
        encoding="utf-8",
    )


def main() -> int:
    iot_env = os.environ.copy()
    iot_env["JWT_SECRET"] = "local-demo-only-secret-2026-08-20-at-least-32-bytes"
    iot_env["JWT_EXPIRES_MINUTES"] = "1440"
    iot_env["PYTHONUTF8"] = "1"
    iot_env["DATA_DIR"] = str(RUNTIME_ROOT / "iot-data")
    iot_env["KEYS_DIR"] = str(RUNTIME_ROOT / "iot-keys")
    iot_env["UV_CACHE_DIR"] = str(RUNTIME_ROOT / "uv-cache")
    iot_env["UV_PYTHON"] = sys.executable
    iot_env["UV_PYTHON_DOWNLOADS"] = "never"
    iot_env["npm_config_cache"] = str(RUNTIME_ROOT / "npm-cache")

    try:
        ensure_iot_runtime(iot_env)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    required = [
        APK_PYTHON,
        IOT_PYTHON,
        APK_ROOT / "FrontendUI" / "dist" / "index.html",
        IOT_ROOT / "frontend" / "dist" / "index.html",
        ROOT / "account_api.py",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        print("ERROR: Required files are missing:")
        for path in missing:
            print(f"  - {path}")
        return 1

    processes: list[subprocess.Popen[bytes]] = []

    def start(name: str, port: int, command: list[str], cwd: Path, env=None) -> None:
        if port_open(port):
            print(f"[ready] {name} already uses port {port}")
            return
        print(f"[start] {name} on port {port}")
        processes.append(
            subprocess.Popen(command, cwd=cwd, env=env or os.environ.copy())
        )

    apk_env = os.environ.copy()
    apk_env["CELERY_TASK_ALWAYS_EAGER"] = "1"
    try:
        ensure_iot_demo_user(iot_env)
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    prepare_apk_frontend()

    start(
        "Shared account API",
        8200,
        [
            str(APK_PYTHON),
            "-m",
            "uvicorn",
            "account_api:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8200",
        ],
        ROOT,
    )
    start(
        "Apionix portal",
        8080,
        [
            str(APK_PYTHON),
            str(ROOT / "serve_static.py"),
            "--directory",
            str(ROOT),
            "--port",
            "8080",
            "--bind",
            "127.0.0.1",
            "--proxy-api",
            "http://127.0.0.1:8200",
            "--proxy-prefix",
            "/account-api/",
            "--proxy-strip-prefix",
        ],
        ROOT,
    )
    start("APK frontend", 5173, [str(APK_PYTHON), str(ROOT / "serve_static.py"), "--directory", str(APK_FRONTEND_RUNTIME), "--port", "5173", "--bind", "127.0.0.1", "--spa-fallback"], ROOT)
    start("APK API", 8000, [str(APK_PYTHON), "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", "8000"], APK_ROOT / "apk-platform", apk_env)
    start("IoT API", 8100, [str(IOT_PYTHON), "-m", "uvicorn", "main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8100"], IOT_ROOT, iot_env)

    if not wait_http("http://127.0.0.1:8100/docs"):
        print("ERROR: IoT API did not become ready.")
        return 1
    try:
        prepare_iot_frontend(fetch_iot_demo_session())
    except Exception as exc:
        print(f"ERROR: Could not prepare the IoT local demo session: {exc}")
        return 1

    start(
        "IoT frontend",
        5180,
        [
            str(IOT_PYTHON),
            str(ROOT / "serve_static.py"),
            "--directory",
            str(IOT_FRONTEND_RUNTIME),
            "--port",
            "5180",
            "--bind",
            "127.0.0.1",
            "--proxy-api",
            "http://127.0.0.1:8100",
            "--proxy-prefix",
            "/backend/",
            "--proxy-strip-prefix",
            "--spa-fallback",
        ],
        ROOT,
    )

    checks = [
        ("Apionix", "http://127.0.0.1:8080/"),
        ("Shared account API", "http://127.0.0.1:8080/account-api/docs"),
        ("APK", "http://127.0.0.1:5173/"),
        ("IoT", "http://127.0.0.1:5180/"),
        ("IoT API proxy", "http://127.0.0.1:5180/backend/docs"),
    ]
    for name, url in checks:
        if not wait_http(url):
            print(f"ERROR: {name} did not become ready: {url}")
            return 1
        print(f"[ready] {name}")

    url = "http://127.0.0.1:8080/"
    print("\nApionix is ready:")
    print(url)
    print("IoT opens directly in a local demo admin session (no login form).")
    print("Keep this window open. Press Ctrl+C to stop services.\n")
    webbrowser.open(url)

    try:
        while True:
            failed = [process for process in processes if process.poll() is not None]
            if failed:
                print("ERROR: A service stopped unexpectedly.")
                return 1
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping Apionix services...")
        return 0
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
