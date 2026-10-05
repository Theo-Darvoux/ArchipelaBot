"""Runs a real Archipelago MultiServer for integration tests (see scripts/setup-dev-server.sh)."""

import socket
import subprocess
import time
from pathlib import Path

import pytest

DEV_DIR = Path(__file__).parents[1] / ".dev"
AP_DIR = DEV_DIR / "Archipelago"
SEEDS = sorted((DEV_DIR / "seeds").glob("AP_*.zip"))

requires_ap_server = pytest.mark.skipif(
    not (AP_DIR / ".venv").exists() or not SEEDS, reason="run scripts/setup-dev-server.sh first"
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(port: int, process: subprocess.Popen, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"MultiServer exited:\n{process.stdout.read()}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    raise TimeoutError("MultiServer did not start")


class APServer:
    def __init__(self, password: str | None = None, port: int | None = None) -> None:
        self.port = port or free_port()
        args = [
            str(AP_DIR / ".venv" / "bin" / "python"), "MultiServer.py", str(SEEDS[-1]),
            "--host", "127.0.0.1", "--port", str(self.port), "--disable_save",
            "--release_mode", "enabled", "--collect_mode", "enabled",
        ]  # fmt: skip
        if password:
            args += ["--password", password]
        self.process = subprocess.Popen(
            args, cwd=AP_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        wait_for_port(self.port, self.process)

    @property
    def address(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def command(self, line: str) -> None:
        """Type a command in the server console (e.g. "/release Alice")."""
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(10)


@pytest.fixture
def ap_server():
    server = APServer()
    yield server
    server.stop()
