"""Minimal stand-in for the Vercel Functions host (DEC-001 local proof). LOCAL USE ONLY.

Runs inside a linux/amd64 container with a packaged function mounted at
/var/task. It reproduces only the process boundary the platform provides:

1. listens on a Unix socket and passes its path as ``VERCEL_IPC_PATH``;
2. starts the function's generated handler module (``vc__handler__python``),
   which imports the app and serves it with Vercel's vendored uvicorn on an
   ephemeral port;
3. records the NUL-delimited JSON IPC messages (``server-started``,
   ``handler-started``, ``end``, ``log``) to ``$RESULTS_DIR/ipc.jsonl``;
4. in ``serve`` mode, forwards TCP from ``0.0.0.0:$FORWARD_PORT`` to the
   announced port so the host can send requests; in ``once`` mode, sends one
   request, records the cold-start timings, and exits.

Standard library only: the function's dependencies stay in /var/task/_vendor.
What it does not reproduce (routing, payload limits, timeouts, scaling,
freezing) is listed in README.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time

TASK_ROOT = os.environ.get("TASK_ROOT", "/var/task")
RESULTS_DIR = os.environ.get("RESULTS_DIR", "/results")
FORWARD_PORT = int(os.environ.get("FORWARD_PORT", "8920"))
IPC_PATH = "/tmp/vc-ipc.sock"  # noqa: S108 (inside a disposable container)
STARTUP_TIMEOUT_SECONDS = 120


def start_runtime(log_name: str) -> tuple[subprocess.Popen[bytes], dict[str, object], float]:
    """Start the packaged function; return the process, its server-started payload, and spawn time."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(IPC_PATH)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(IPC_PATH)
    listener.listen(1)
    listener.settimeout(STARTUP_TIMEOUT_SECONDS)

    spawned_at = time.monotonic()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import vc__handler__python"],
        cwd=TASK_ROOT,
        env=dict(os.environ, VERCEL_IPC_PATH=IPC_PATH),
    )
    conn, _ = listener.accept()
    started = threading.Event()
    payload: dict[str, object] = {}

    def read_messages() -> None:
        buffer = b""
        with open(os.path.join(RESULTS_DIR, log_name), "a", encoding="utf-8") as log:
            while chunk := conn.recv(65536):
                buffer += chunk
                while b"\0" in buffer:
                    raw, buffer = buffer.split(b"\0", 1)
                    message = json.loads(raw)
                    log.write(json.dumps({"t": round(time.monotonic() - spawned_at, 4), **message}) + "\n")
                    log.flush()
                    if message.get("type") == "server-started":
                        payload.update(message["payload"])
                        started.set()

    threading.Thread(target=read_messages, daemon=True).start()
    while not started.wait(0.25):
        if proc.poll() is not None:
            sys.exit(f"the runtime exited with code {proc.returncode} before announcing server-started")
        if time.monotonic() - spawned_at > STARTUP_TIMEOUT_SECONDS:
            proc.kill()
            sys.exit("the runtime did not announce server-started")
    return proc, payload, spawned_at


async def forward(target_port: int, proc: subprocess.Popen[bytes]) -> None:
    async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except ConnectionError:
            pass
        finally:
            writer.close()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", target_port)
        except OSError:
            writer.close()
            return
        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))

    server = await asyncio.start_server(handle, "0.0.0.0", FORWARD_PORT)  # noqa: S104 (container-internal)
    async with server:
        while proc.poll() is None:
            await asyncio.sleep(0.5)
    sys.exit(f"the runtime exited with code {proc.returncode}")


def once(path: str) -> None:
    """One cold start: spawn, wait for server-started, send one request, record timings."""
    proc, payload, spawned_at = start_runtime("ipc-coldstart.jsonl")
    ready_at = time.monotonic()
    connection = http.client.HTTPConnection("127.0.0.1", int(str(payload["httpPort"])), timeout=30)
    connection.request("GET", path)
    response = connection.getresponse()
    response.read()
    done_at = time.monotonic()
    proc.terminate()
    record = {
        "path": path,
        "status": response.status,
        "spawn_to_server_started_ms": round((ready_at - spawned_at) * 1000, 1),
        "spawn_to_first_response_ms": round((done_at - spawned_at) * 1000, 1),
        "first_request_ms": round((done_at - ready_at) * 1000, 1),
        "runtime_init_duration_ms": payload.get("initDuration"),
        "runtime_user_init_duration_ms": payload.get("userInitDuration"),
    }
    with open(os.path.join(RESULTS_DIR, "coldstart.jsonl"), "a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if mode == "serve":
        proc, payload, _ = start_runtime("ipc.jsonl")
        asyncio.run(forward(int(str(payload["httpPort"])), proc))
    elif mode == "once":
        once(sys.argv[2] if len(sys.argv) > 2 else "/api/v1/health")
    else:
        sys.exit(f"unknown mode {mode!r}; use serve or once")


if __name__ == "__main__":
    main()
