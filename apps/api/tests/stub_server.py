"""A loopback-only HTTP stub for exercising real SDK clients without network access."""

from __future__ import annotations

import gzip
import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        raw = gzip.decompress(self.body) if self.headers.get("content-encoding") == "gzip" else self.body
        return json.loads(raw)


@dataclass
class Stub:
    url: str
    requests: list[Recorded] = field(default_factory=list)


Responder = Callable[[Recorded], tuple[int, Any]]


@contextmanager
def stub_server(respond: Responder) -> Iterator[Stub]:
    stub = Stub(url="")

    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            recorded = Recorded(
                method=self.command,
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=self.rfile.read(length) if length else b"",
            )
            stub.requests.append(recorded)
            status, payload = respond(recorded)
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _handle  # noqa: N815

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    stub.url = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
