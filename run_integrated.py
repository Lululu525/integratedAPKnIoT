"""Start and supervise the complete local Apionix demo stack."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parent
APK_ROOT = WORKSPACE / "apk-analysis-platform"
IOT_ROOT = WORKSPACE / "ESP-Firmware-Over-The-Air"
APK_PYTHON = APK_ROOT / ".venv" / "Scripts" / "python.exe"
IOT_PYTHON = IOT_ROOT / ".venv" / "Scripts" / "python.exe"
NPM = "npm.cmd" if os.name == "nt" else "npm"


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


def main() -> int:
    required = [
        APK_PYTHON,
        IOT_PYTHON,
        APK_ROOT / "FrontendUI" / "dist" / "index.html",
        IOT_ROOT / "frontend" / "node_modules",
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
    iot_env = os.environ.copy()
    iot_env["JWT_SECRET"] = "local-demo-only-secret-2026-08-20-at-least-32-bytes"
    iot_ui_env = iot_env.copy()
    iot_ui_env["VITE_BACKEND"] = "http://127.0.0.1:8100"

    start("Apionix portal", 8080, [str(APK_PYTHON), str(ROOT / "serve_static.py"), "--directory", str(ROOT), "--port", "8080", "--bind", "127.0.0.1"], ROOT)
    start("APK frontend", 5173, [str(APK_PYTHON), str(ROOT / "serve_static.py"), "--directory", str(APK_ROOT / "FrontendUI" / "dist"), "--port", "5173", "--bind", "127.0.0.1"], ROOT)
    start("APK API", 8000, [str(APK_PYTHON), "-m", "uvicorn", "apps.api.main:app", "--host", "127.0.0.1", "--port", "8000"], APK_ROOT / "apk-platform", apk_env)
    start("IoT API", 8100, [str(IOT_PYTHON), "-m", "uvicorn", "main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "8100"], IOT_ROOT, iot_env)
    start("IoT frontend", 5180, [NPM, "run", "dev", "--", "--host", "127.0.0.1", "--port", "5180"], IOT_ROOT / "frontend", iot_ui_env)

    checks = [
        ("Apionix", "http://127.0.0.1:8080/"),
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
