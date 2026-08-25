"""Serve static files with browser-safe MIME types on Windows."""

import argparse
import functools
import mimetypes
from pathlib import Path
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class StaticHandler(SimpleHTTPRequestHandler):
    proxy_api = None
    proxy_prefix = "/v1/"
    proxy_strip_prefix = False
    spa_fallback = False
    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".css": "text/css",
        ".js": "application/javascript",
        ".mjs": "application/javascript",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".wasm": "application/wasm",
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def _proxy_request(self) -> None:
        if not self.proxy_api:
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length) if content_length else None
        proxy_path = self.path
        if self.proxy_strip_prefix and proxy_path.startswith(self.proxy_prefix):
            proxy_path = proxy_path[len(self.proxy_prefix) - 1 :]
        target = f"{self.proxy_api.rstrip('/')}{proxy_path}"
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        request = Request(target, data=body, headers=headers, method=self.command)
        try:
            response = urlopen(request, timeout=600)
        except HTTPError as exc:
            response = exc
        except URLError as exc:
            self.send_error(502, f"Local API unavailable: {exc.reason}")
            return

        with response:
            payload = response.read()
            self.send_response(response.status)
            for key, value in response.headers.items():
                if key.lower() not in {
                    "connection",
                    "content-length",
                    "transfer-encoding",
                    "content-encoding",
                }:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

    def _is_proxy_request(self) -> bool:
        return bool(self.proxy_api and self.path.startswith(self.proxy_prefix))

    def do_GET(self) -> None:
        if self._is_proxy_request():
            self._proxy_request()
            return
        requested_file = Path(self.translate_path(self.path.split("?", 1)[0]))
        if self.spa_fallback and not requested_file.exists():
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        if self._is_proxy_request():
            self._proxy_request()
            return
        self.send_error(404)

    def do_OPTIONS(self) -> None:
        if self._is_proxy_request():
            self._proxy_request()
            return
        self.send_error(404)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--proxy-api")
    parser.add_argument("--proxy-prefix", default="/v1/")
    parser.add_argument("--proxy-strip-prefix", action="store_true")
    parser.add_argument("--spa-fallback", action="store_true")
    args = parser.parse_args()

    mimetypes.add_type("application/javascript", ".js", strict=True)
    mimetypes.add_type("application/javascript", ".mjs", strict=True)
    StaticHandler.proxy_api = args.proxy_api
    StaticHandler.proxy_prefix = args.proxy_prefix
    StaticHandler.proxy_strip_prefix = args.proxy_strip_prefix
    StaticHandler.spa_fallback = args.spa_fallback
    handler = functools.partial(StaticHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {args.directory} at http://{args.bind}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
