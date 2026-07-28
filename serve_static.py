"""Serve static files with browser-safe MIME types on Windows."""

import argparse
import functools
import mimetypes
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class StaticHandler(SimpleHTTPRequestHandler):
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--bind", default="127.0.0.1")
    args = parser.parse_args()

    mimetypes.add_type("application/javascript", ".js", strict=True)
    mimetypes.add_type("application/javascript", ".mjs", strict=True)
    handler = functools.partial(StaticHandler, directory=args.directory)
    server = ThreadingHTTPServer((args.bind, args.port), handler)
    print(f"Serving {args.directory} at http://{args.bind}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

