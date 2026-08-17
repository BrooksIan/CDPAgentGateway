#!/usr/bin/env python3
"""Minimal Knox/CDP stand-in for local Agent Gateway tests."""

from __future__ import annotations

import base64
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

JWKS_PATH = Path(os.environ.get("JWKS_PATH", "/keys/jwks.json"))
PORT = int(os.environ.get("PORT", "8080"))
PROXY_PREFIX = os.environ.get("KNOX_PROXY_PREFIX", "/gateway/cdp-proxy-token")


def b64url_json(segment: str) -> dict:
    pad = "=" * (-len(segment) % 4)
    raw = base64.urlsafe_b64decode(segment + pad)
    return json.loads(raw.decode("utf-8"))


def subject_from_authorization(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    try:
        payload = b64url_json(token.split(".")[1])
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"mock-cdp: {self.address_string()} {fmt % args}")

    def _send(self, status: int, body: dict | list, extra: dict[str, str] | None = None) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if extra:
            for key, value in extra.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in {"/health", "/"}:
            self._send(200, {"status": "ok", "service": "mock-cdp"})
            return

        if path in {
            "/gateway/homepage/knoxtoken/api/v1/jwks.json",
            "/jwks.json",
        }:
            self._send(200, json.loads(JWKS_PATH.read_text()))
            return

        if path == "/.well-known/openid-configuration":
            self._send(
                200,
                {
                    "issuer": "KNOXSSO",
                    "jwks_uri": "http://mock-cdp:8080/gateway/homepage/knoxtoken/api/v1/jwks.json",
                    "id_token_signing_alg_values_supported": ["RS256"],
                },
            )
            return

        if "/livy_for_spark3" in path:
            user = self._require_knox(parsed)
            if user is None:
                return
            self._send_livy(path, parsed.query, user)
            return

        if path == f"{PROXY_PREFIX}/whoami" or path == "/whoami":
            self._require_knox(parsed)
            return

        if path.startswith(f"{PROXY_PREFIX}/webhdfs/v1") or path.startswith("/webhdfs/v1"):
            user = self._require_knox(parsed)
            if user is None:
                return
            self._send(
                200,
                {
                    "FileStatuses": {
                        "FileStatus": [
                            {
                                "pathSuffix": "tmp",
                                "type": "DIRECTORY",
                                "owner": user,
                            }
                        ]
                    },
                    "knox_user": user,
                    "op": "LISTSTATUS",
                },
            )
            return

        self._send(404, {"error": "not_found", "path": path})

    def _require_knox(self, parsed) -> str | None:
        user = subject_from_authorization(self.headers.get("Authorization"))
        forwarded = self.headers.get("X-Knox-User")
        if not user:
            self._send(401, {"error": "unauthorized", "reason": "missing_or_invalid_bearer"})
            return None
        if parsed.path.endswith("/whoami") or parsed.path == "/whoami":
            self._send(
                200,
                {
                    "sub": user,
                    "x_knox_user": forwarded,
                    "token_id": self.headers.get("X-Knox-Token-Id"),
                    "authorization_present": True,
                },
            )
            return None
        return user

    def _send_livy(self, path: str, query: str, user: str) -> None:
        rest = path.split("/livy_for_spark3", 1)[-1].rstrip("/") or "/"
        batch = {
            "id": 0,
            "name": "mock-job",
            "state": "success",
            "appId": "application_1",
            "owner": user,
        }
        if rest in {"/", "/sessions"}:
            self._send(200, {"from": 0, "total": 0, "sessions": [], "knox_user": user})
            return
        if rest == "/batches":
            self._send(200, {"from": 0, "total": 1, "sessions": [batch], "knox_user": user})
            return
        if rest == "/batches/0":
            self._send(200, batch)
            return
        if rest == "/batches/0/log":
            params = parse_qs(query)
            size = int((params.get("size") or ["80"])[0])
            lines = [f"line-{i} owner={user}" for i in range(size)]
            self._send(200, {"id": 0, "from": 0, "size": len(lines), "log": lines})
            return
        self._send(404, {"error": "not_found", "path": rest})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if "/livy_for_spark3" not in path:
            self._send(404, {"error": "not_found", "path": path})
            return
        user = self._require_knox(parsed)
        if user is None:
            return
        rest = path.split("/livy_for_spark3", 1)[-1].rstrip("/") or "/"
        if rest != "/batches":
            self._send(405, {"error": "method_not_allowed", "path": rest})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid_json"})
            return
        if not isinstance(body, dict) or not body.get("file"):
            self._send(400, {"error": "file_required"})
            return
        created = {
            "id": 1,
            "name": body.get("name") or "mock-job",
            "state": "starting",
            "owner": user,
        }
        self._send(201, created)


def main() -> None:
    if not JWKS_PATH.exists():
        raise SystemExit(f"JWKS file not found: {JWKS_PATH}")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"mock-cdp listening on {PORT}, jwks={JWKS_PATH}")
    server.serve_forever()


if __name__ == "__main__":
    main()
