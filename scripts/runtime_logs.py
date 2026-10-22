"""Bounded diagnostic streams; raw stdout protocols never pass through redaction."""
from __future__ import annotations

import codecs
import hashlib
import os
import re
import threading
from pathlib import Path

REDACTED = "[REDACTED]"
_ASSIGNMENT = re.compile(
    r"""(?ix)(\b(?:[\w-]*(?:token|password|passwd|secret|api[_-]?key)[\w-]*)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,"';}]+)"""
)
_AUTH = re.compile(r"(?i)(authorization[\x22\x27]?\s*[:=]\s*[\x22\x27]?)([^\r\n]+)")
_URL = re.compile(r"(https?://)[^\s/@]+:[^\s/@]+@", re.I)
_ARG = re.compile(r"""(?i)(--[\w-]*(?:token|password|secret|api-key)[\w-]*\s+)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s]+)""")
_KEY = re.compile(r"(?i)token|password|passwd|secret|authorization|api[_-]?key")


def redact(text: str, secrets=()) -> str:
    for value in secrets:
        if value:
            text = text.replace(value, REDACTED)
    text = _URL.sub(r"\1[REDACTED]@", text)
    text = _AUTH.sub(r"\1[REDACTED]", text)
    text = _ASSIGNMENT.sub(r"\1[REDACTED]", text)
    return _ARG.sub(r"\1[REDACTED]", text)


def redact_object(value, secrets=()):
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [redact_object(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: REDACTED if _KEY.search(str(key)) else redact_object(item, secrets)
                for key, item in value.items()}
    return value


class StreamLog:
    """Sanitize complete bounded lines, including secrets spanning pipe reads."""
    MAX_LINE_CHARS = 16384

    def __init__(self, folder: Path, stream: str, cfg: dict, budget: int, secrets=()):
        self.folder, self.stream = folder, stream
        self.segment = min(cfg["segment_bytes"], 16 * 1024 * 1024)
        self.limit = min(cfg["max_bytes_per_stream"], budget) if cfg["mode"] == "bounded" else 0
        self.secrets = tuple(secrets)
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.line, self.tail = "", ""
        self.discard_line = False
        self.lock = threading.Lock()
        self.metadata = {"observed_bytes": 0, "captured_bytes": 0, "dropped_bytes": 0,
                         "truncated": False, "redacted": False, "capture_error": None,
                         "encoding": "utf-8", "segments": []}
        self.file = self.hasher = None
        self.closed = False

    def write(self, chunk: bytes) -> None:
        with self.lock:
            self.metadata["observed_bytes"] += len(chunk)
            # Incremental decoding also bounds a caller supplying a large chunk.
            for offset in range(0, len(chunk), 4096):
                self._text(self.decoder.decode(chunk[offset:offset + 4096]))

    def _text(self, text: str) -> None:
        for part in text.splitlines(keepends=True):
            newline = part.endswith(("\n", "\r"))
            if not self.discard_line:
                if len(self.line) + len(part) > self.MAX_LINE_CHARS:
                    self.line = ""
                    self.discard_line = True
                    self.metadata.update(redacted=True, truncated=True)
                else:
                    self.line += part
            if newline:
                self._emit("[long line omitted]\n" if self.discard_line else self.line)
                self.line = ""
                self.discard_line = False

    def _emit(self, text: str) -> None:
        clean = redact(text, self.secrets)
        self.tail = (self.tail + clean)[-4000:]
        self.metadata["redacted"] |= clean != text
        if self.metadata["capture_error"]:
            return
        data = clean.encode("utf-8")
        remaining = self.limit - self.metadata["captured_bytes"]
        self.metadata["truncated"] |= len(data) > remaining
        data = data[:max(0, remaining)]
        try:
            while data:
                if self.file is None or self.metadata["segments"][-1]["bytes"] >= self.segment:
                    self._close_segment()
                    name = f"{self.stream}.{len(self.metadata['segments']) + 1:04d}.log"
                    path = self.folder / name
                    self.file = path.open("xb")
                    if os.name != "nt":
                        path.chmod(0o600)
                    self.hasher = hashlib.sha256()
                    self.metadata["segments"].append({"path": name, "bytes": 0})
                entry = self.metadata["segments"][-1]
                block = data[:self.segment - entry["bytes"]]
                self.file.write(block)
                self.hasher.update(block)
                entry["bytes"] += len(block)
                self.metadata["captured_bytes"] += len(block)
                data = data[len(block):]
        except (OSError, ValueError) as exc:
            self.metadata.update(capture_error=type(exc).__name__, truncated=True)

    def _close_segment(self) -> None:
        if self.file is not None:
            try:
                self.file.flush()
                os.fsync(self.file.fileno())
                self.metadata["segments"][-1]["sha256"] = self.hasher.hexdigest()
            finally:
                self.file.close()
                self.file = None

    def close(self) -> dict:
        with self.lock:
            if not self.closed:
                self._text(self.decoder.decode(b"", final=True))
                if self.discard_line:
                    self._emit("[long line omitted]\n")
                elif self.line:
                    self._emit(self.line)
                self.line = ""
                try:
                    self._close_segment()
                except (OSError, ValueError) as exc:
                    self.metadata.update(capture_error=type(exc).__name__, truncated=True)
                # Raw-byte / sanitized-byte difference, including redaction.
                self.metadata["dropped_bytes"] = max(0, self.metadata["observed_bytes"]
                                                      - self.metadata["captured_bytes"])
                self.closed = True
            return self.metadata


class LogSink:
    def __init__(self, folder: Path, cfg: dict, budget: int, secrets=()):
        self.streams = {name: StreamLog(folder, name, cfg, budget // 2, secrets)
                        for name in ("stdout", "stderr")}

    def write(self, index: int, chunk: bytes) -> None:
        self.streams[("stdout", "stderr")[index]].write(chunk)

    def close(self) -> dict:
        return {name: stream.close() for name, stream in self.streams.items()}

    def tails(self) -> dict:
        return {name + "_tail": stream.tail for name, stream in self.streams.items()}
