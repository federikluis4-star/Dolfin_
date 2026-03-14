#!/usr/bin/env python3
"""
Shared runtime helpers for local UI wrappers around bot.py.
"""

from __future__ import annotations

import os
import pty
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
BOT_PATH = PROJECT_ROOT / "bot.py"
MAX_BUFFER_CHARS = 200_000


class BotSessionManager:
    """Run bot.py in a PTY and expose a tail-like state API."""

    def __init__(self, bot_path: Path = BOT_PATH):
        self.bot_path = bot_path
        self.lock = threading.Lock()
        self.process: subprocess.Popen[bytes] | None = None
        self.master_fd: int | None = None
        self.reader_thread: threading.Thread | None = None
        self.started_at: str | None = None
        self.exit_code: int | None = None
        self.buffer = ""
        self.base_cursor = 0

    def _append_output(self, text: str) -> None:
        with self.lock:
            self.buffer += text
            overflow = len(self.buffer) - MAX_BUFFER_CHARS
            if overflow > 0:
                self.buffer = self.buffer[overflow:]
                self.base_cursor += overflow

    def _reader_loop(self, master_fd: int) -> None:
        while True:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            self._append_output(chunk.decode("utf-8", errors="replace"))

        try:
            os.close(master_fd)
        except OSError:
            pass

        with self.lock:
            if self.master_fd == master_fd:
                self.master_fd = None

    def start(self) -> dict:
        with self.lock:
            if self.process and self.process.poll() is None:
                return self._build_state_locked(self.base_cursor)

            self.process = None
            self.master_fd = None
            self.reader_thread = None
            self.started_at = datetime.now().astimezone().isoformat()
            self.exit_code = None
            self.buffer = ""
            self.base_cursor = 0

        master_fd, slave_fd = pty.openpty()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")

        process = subprocess.Popen(
            [sys.executable, str(self.bot_path)],
            cwd=str(self.bot_path.parent),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            start_new_session=True,
        )
        os.close(slave_fd)

        reader = threading.Thread(
            target=self._reader_loop,
            args=(master_fd,),
            name="bot-ui-reader",
            daemon=True,
        )
        reader.start()

        with self.lock:
            self.process = process
            self.master_fd = master_fd
            self.reader_thread = reader
            return self._build_state_locked(self.base_cursor)

    def stop(self) -> dict:
        with self.lock:
            process = self.process
        if not process or process.poll() is not None:
            return self.get_state(0)

        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        finally:
            with self.lock:
                self.exit_code = process.poll()

        return self.get_state(0)

    def send(self, text: str, block: bool = False) -> dict:
        payload = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        if block:
            payload = payload.rstrip("\n") + "\n\n"
        else:
            payload = payload.rstrip("\n") + "\n"

        with self.lock:
            process = self.process
            master_fd = self.master_fd

        if not process or process.poll() is not None or master_fd is None:
            raise RuntimeError("Бот не запущен.")

        try:
            os.write(master_fd, payload.encode("utf-8"))
        except OSError as exc:
            raise RuntimeError(f"Не удалось передать ввод: {exc}") from exc

        return self.get_state(0)

    def get_state(self, cursor: int) -> dict:
        with self.lock:
            if self.process and self.process.poll() is not None:
                self.exit_code = self.process.poll()
            return self._build_state_locked(cursor)

    def _build_state_locked(self, cursor: int) -> dict:
        if cursor < self.base_cursor:
            output = self.buffer
            reset_cursor = True
        else:
            start = cursor - self.base_cursor
            output = self.buffer[start:]
            reset_cursor = False

        running = bool(self.process and self.process.poll() is None)
        return {
            "running": running,
            "started_at": self.started_at,
            "exit_code": self.exit_code if not running else None,
            "cursor": self.base_cursor + len(self.buffer),
            "output": output,
            "reset_cursor": reset_cursor,
        }
