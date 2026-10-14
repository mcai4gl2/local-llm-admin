"""A local stand-in for the Hugging Face API and file host, for the download tests.

Two host names reach the same server: ``127.0.0.1`` is "huggingface.co" and
``localhost`` is the CDN that LFS files redirect to, so tests can check where the
token was sent. Knobs on :class:`FakeHF` inject failures per file path.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlsplit


def git_blob_sha1(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


@dataclass
class Repo:
    sha: str
    files: dict[str, bytes]
    gated: bool = False
    lfs: tuple[str, ...] = (".safetensors",)
    api_sha: str | None = None  # the API reports this commit instead (revision tests)
    extra_siblings: list[dict[str, Any]] = field(default_factory=list)

    def is_lfs(self, path: str) -> bool:
        return path.endswith(self.lfs)


@dataclass
class FakeHF:
    repos: dict[str, Repo] = field(default_factory=dict)
    token: str | None = None  # required for gated repos
    ignore_range: bool = False
    fail_times: dict[str, int] = field(default_factory=dict)  # path -> 500s to send first
    corrupt: set[str] = field(default_factory=set)
    cut_once: set[str] = field(default_factory=set)  # send half, then drop the connection
    slow: dict[str, float] = field(default_factory=dict)  # path -> seconds per 64 KiB
    requests: list[dict[str, Any]] = field(default_factory=list)
    port: int = 0

    @property
    def endpoint(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def siblings(self, repo: Repo) -> list[dict[str, Any]]:
        out = []
        for path, data in sorted(repo.files.items()):
            s: dict[str, Any] = {
                "rfilename": path,
                "blobId": git_blob_sha1(data),
                "size": len(data),
            }
            if repo.is_lfs(path):
                s["lfs"] = {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                    "pointerSize": 133,
                }
            out.append(s)
        return out + repo.extra_siblings

    def ranges(self, path: str) -> list[str | None]:
        return [r["range"] for r in self.requests if r["path"].endswith("/" + path)]


def serve(hf: FakeHF) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:
            pass

        def _send(
            self, code: int, body: bytes = b"", headers: dict[str, str] | None = None
        ) -> None:
            self.send_response(code)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, repo: Repo) -> bool:
            return not repo.gated or self.headers.get("Authorization") == f"Bearer {hf.token}"

        def do_GET(self) -> None:  # noqa: N802
            u = urlsplit(self.path)
            host = (self.headers.get("Host") or "").split(":")[0]
            hf.requests.append(
                {
                    "host": host,
                    "path": unquote(u.path),
                    "auth": self.headers.get("Authorization"),
                    "range": self.headers.get("Range"),
                }
            )
            parts = unquote(u.path).strip("/").split("/")
            if parts[:2] == ["api", "models"] and len(parts) >= 6 and parts[4] == "revision":
                repo = hf.repos.get(f"{parts[2]}/{parts[3]}")
                if repo is None:
                    return self._send(404, b"{}")
                if not self._authorized(repo):
                    return self._send(401, b"{}")
                doc = {
                    "id": f"{parts[2]}/{parts[3]}",
                    "sha": repo.api_sha or repo.sha,
                    "gated": "manual" if repo.gated else False,
                    "private": False,
                    "siblings": hf.siblings(repo),
                }
                return self._send(200, json.dumps(doc).encode())
            if parts and parts[0] == "cdn" and host == "localhost":
                key = "/".join(parts[1:])
                return self._file(key.split("/", 3)[-1], hf.repos[f"{parts[1]}/{parts[2]}"])
            if len(parts) >= 5 and parts[2] == "resolve":
                repo = hf.repos.get(f"{parts[0]}/{parts[1]}")
                if repo is None or parts[3] != repo.sha:
                    return self._send(404)
                if not self._authorized(repo):
                    return self._send(401)
                path = "/".join(parts[4:])
                if repo.is_lfs(path) and host == "127.0.0.1":
                    loc = (
                        f"http://localhost:{hf.port}/cdn/{parts[0]}/{parts[1]}/{path}?sig=SECRETSIG"
                    )
                    return self._send(302, headers={"Location": loc})
                return self._file(path, repo)
            return self._send(404)

        def _file(self, path: str, repo: Repo) -> None:
            if path not in repo.files:
                return self._send(404)
            if hf.fail_times.get(path, 0) > 0:
                hf.fail_times[path] -= 1
                return self._send(500, b"busy")
            data = repo.files[path]
            if path in hf.corrupt:
                data = bytes(b ^ 1 for b in data)
            start = 0
            rng = self.headers.get("Range")
            if rng and not hf.ignore_range:
                start = int(rng.split("=")[1].split("-")[0])
                if start >= len(data):
                    return self._send(416)
            body = data[start:]
            code = 206 if start or (rng and not hf.ignore_range) else 200
            self.send_response(code)
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if path in hf.cut_once:
                hf.cut_once.discard(path)
                self.wfile.write(body[: len(body) // 2])
                self.wfile.flush()
                self.close_connection = True
                self.connection.shutdown(2)
                return
            delay = hf.slow.get(path)
            for i in range(0, len(body), 65536):
                if delay:
                    time.sleep(delay)
                try:
                    self.wfile.write(body[i : i + 65536])
                except OSError:
                    return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    hf.port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
