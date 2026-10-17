# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Console progress and timing helpers for ``tads scan``.

Operational telemetry only — not stored in public reports. Wall-clock time is
for display timestamps; elapsed durations use monotonic time.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, TextIO

# Live analysis / repair / correction progress (single-line on TTY).
_LIVE_PREFIXES = (
    "analyzing chunk ",
    "analyzing whole document",
    "analyzing section ",
    "correcting evidence",
    "repairing json",
)

_CHUNK_RE = re.compile(
    r"^analyzing chunk (?P<cur>\d+)/(?P<total>\d+) \((?P<sections>\d+) sections?\)$",
    re.IGNORECASE,
)
_CORRECT_CHUNK_RE = re.compile(
    r"^correcting evidence grounding(?: for chunk (?P<cur>\d+)/(?P<total>\d+))?$",
    re.IGNORECASE,
)
_REPAIR_CHUNK_RE = re.compile(
    r"^repairing JSON(?: for chunk (?P<cur>\d+)/(?P<total>\d+))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ProgressClocks:
    """Injectable clocks for deterministic tests."""

    wall_now: Callable[[], datetime]
    monotonic: Callable[[], float]


def system_clocks() -> ProgressClocks:
    return ProgressClocks(
        wall_now=lambda: datetime.now(timezone.utc),
        monotonic=__import__("time").monotonic,
    )


def format_utc_timestamp(moment: datetime) -> str:
    """Format ``[YYYY-MM-DDTHH:MM:SS.mmmZ]`` (UTC, millisecond precision)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    else:
        moment = moment.astimezone(timezone.utc)
    # Floor to milliseconds for stable display (avoid float rounding to .1000).
    ms = moment.microsecond // 1000
    base = moment.strftime("%Y-%m-%dT%H:%M:%S")
    return f"[{base}.{ms:03d}Z]"


def format_duration(seconds: float) -> str:
    """Human-readable nonnegative duration with one decimal place of seconds.

    Examples: ``0.0s``, ``0.3s``, ``42.8s``, ``12m 08.4s``, ``1h 22m 10.5s``.
    Rounding is half-up on the tenth-of-a-second (deterministic for tests).
    """
    if seconds < 0 or seconds != seconds:  # NaN guard
        seconds = 0.0
    # Convert to integer tenths with half-up rounding.
    tenths = int(seconds * 10 + 0.5)
    hours, rem_tenths = divmod(tenths, 36000)
    minutes, sec_tenths = divmod(rem_tenths, 600)
    whole_s, frac = divmod(sec_tenths, 10)
    frac_s = f"{whole_s}.{frac}"
    if hours:
        return f"{hours}h {minutes}m {frac_s}s"
    if minutes:
        return f"{minutes}m {whole_s:02d}.{frac}s"
    return f"{frac_s}s"


def _is_live_message(message: str) -> bool:
    lowered = message.strip().casefold()
    return any(lowered.startswith(prefix) for prefix in _LIVE_PREFIXES)


def _is_timestamped_status(message: str) -> bool:
    """Execution-phase status lines (not every planning bullet)."""
    lowered = message.strip().casefold()
    return (
        lowered.startswith("mode=")
        or lowered.startswith("done:")
        or lowered.startswith("saved raw")
        or "raw diagnostic" in lowered
        or lowered.startswith("raw model output")
    )


class ScanProgressReporter:
    """TTY-aware progress sink for ``tads scan`` execution events.

    Callable as ``on_progress(message: str)`` for the pipeline callback.
    """

    def __init__(
        self,
        stream: TextIO | None = None,
        *,
        clocks: ProgressClocks | None = None,
        isatty: bool | None = None,
    ) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self._clocks = clocks if clocks is not None else system_clocks()
        if isatty is None:
            try:
                self._tty = bool(self._stream.isatty())
            except Exception:  # noqa: BLE001 - treat unknown streams as non-TTY
                self._tty = False
        else:
            self._tty = bool(isatty)
        self._start_mono: float | None = None
        self._live_active = False
        self._live_width = 0
        self._closed = False

    @property
    def is_tty(self) -> bool:
        return self._tty

    def mark_start(self) -> None:
        """Record monotonic start (idempotent if already started)."""
        if self._start_mono is None:
            self._start_mono = self._clocks.monotonic()

    def elapsed_seconds(self) -> float:
        self.mark_start()
        assert self._start_mono is not None
        return max(0.0, self._clocks.monotonic() - self._start_mono)

    def __call__(self, message: str) -> None:
        """Pipeline ``on_progress`` entry point."""
        text = (message or "").strip()
        if not text:
            return
        if _is_live_message(text):
            self.live(self._format_live_body(text))
            return
        if _is_timestamped_status(text):
            self.line(self._format_status_body(text))
            return
        # Scope/planning notes may also arrive via on_progress; keep them
        # readable without timestamps (plan summary stays the primary view).
        self.plain_after_finalize(text)

    def live(self, body: str) -> None:
        """Update or emit a progress line (live on TTY, permanent otherwise)."""
        if self._closed:
            return
        self.mark_start()
        stamped = self._stamp(body)
        if self._tty:
            self._write_live(stamped)
        else:
            self._writeln(stamped)

    def line(self, body: str) -> None:
        """Emit a permanent timestamped line (finalizes any live line first)."""
        if self._closed:
            return
        self.mark_start()
        self.finalize_live()
        self._writeln(self._stamp(body))

    def plain_after_finalize(self, body: str) -> None:
        """Write a non-timestamped permanent line after clearing live progress."""
        if self._closed:
            return
        self.finalize_live()
        self._writeln(body)

    def finalize_live(self) -> None:
        """Clear an active TTY live line; no-op for non-TTY / inactive."""
        if not self._live_active:
            return
        if self._tty:
            # Clear residual characters from a longer prior message.
            self._stream.write("\r" + (" " * self._live_width) + "\r")
            self._stream.flush()
        self._live_active = False
        self._live_width = 0

    def success(self) -> None:
        """Final success summary with total runtime."""
        elapsed = format_duration(self.elapsed_seconds())
        self.line(f"Completed successfully in {elapsed}")
        self._closed = True

    def fail(self, message: str) -> None:
        """Controlled failure: public message + elapsed runtime.

        Callers must pass already-public text (see ``tads.errors``). This method
        still normalizes control characters and Rich/ANSI hazards for one safe
        terminal line.
        """
        from tads.errors import (
            GENERIC_SCAN_FAILURE,
            normalize_public_cli_line,
            public_explicit_scan_message,
        )

        text = public_explicit_scan_message(message or GENERIC_SCAN_FAILURE)
        # public_explicit_scan_message already normalizes; keep a final guard.
        text = normalize_public_cli_line(text) or GENERIC_SCAN_FAILURE
        self.line(text)
        elapsed = format_duration(self.elapsed_seconds())
        self.line(f"Failed after {elapsed}")
        self._closed = True

    def interrupted(self) -> None:
        """KeyboardInterrupt path: elapsed time, clean line, no traceback."""
        elapsed = format_duration(self.elapsed_seconds())
        self.line(f"Interrupted after {elapsed}")
        self._closed = True

    def _stamp(self, body: str) -> str:
        return f"{format_utc_timestamp(self._clocks.wall_now())} {body}"

    def _format_status_body(self, message: str) -> str:
        if message.startswith("mode="):
            return f"Starting analysis ({message})"
        if message == "saved raw model output":
            return "Saved raw diagnostic output"
        if message.casefold().startswith("raw model output not saved"):
            return message
        if message.startswith("done:"):
            # "done: N candidates"
            return f"Analysis complete ({message[len('done:'):].strip()})"
        return message

    def _format_live_body(self, message: str) -> str:
        elapsed = format_duration(self.elapsed_seconds())
        chunk = _CHUNK_RE.match(message.strip())
        if chunk:
            cur = int(chunk.group("cur"))
            total = int(chunk.group("total"))
            sections = int(chunk.group("sections"))
            pct = (100.0 * cur / total) if total > 0 else 0.0
            base = f"Analyzing chunk {cur}/{total} ({sections} sections"
            if self._tty:
                return f"{base}, {pct:.1f}%, elapsed {elapsed})"
            return f"{base})"
        correct = _CORRECT_CHUNK_RE.match(message.strip())
        if correct:
            cur, total = correct.group("cur"), correct.group("total")
            if cur and total:
                base = f"Correcting evidence for chunk {cur}/{total}"
            else:
                base = "Correcting evidence"
            if self._tty:
                return f"{base} (elapsed {elapsed})"
            return base
        repair = _REPAIR_CHUNK_RE.match(message.strip())
        if repair:
            cur, total = repair.group("cur"), repair.group("total")
            if cur and total:
                base = f"Repairing JSON for chunk {cur}/{total}"
            else:
                base = "Repairing JSON"
            if self._tty:
                return f"{base} (elapsed {elapsed})"
            return base
        if message.casefold().startswith("analyzing whole document"):
            base = "Analyzing whole document"
            if self._tty:
                return f"{base} (elapsed {elapsed})"
            return base
        # Fallback: title-case first character, optional elapsed on TTY.
        body = message[:1].upper() + message[1:] if message else message
        if self._tty:
            return f"{body} (elapsed {elapsed})"
        return body

    def _write_live(self, stamped: str) -> None:
        # Pad to prior width so a shorter replacement fully clears leftovers.
        width = max(self._live_width, len(stamped))
        padded = stamped.ljust(width)
        self._stream.write("\r" + padded)
        self._stream.flush()
        self._live_width = len(stamped)
        self._live_active = True

    def _writeln(self, text: str) -> None:
        self._stream.write(text + "\n")
        self._stream.flush()


def is_progress_live_message(message: str) -> bool:
    """Public helper for tests."""
    return _is_live_message(message)
