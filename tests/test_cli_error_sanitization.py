# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""CLI public-error boundary: only PublicScanError messages cross stdout."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from tads.cli import app
from tads.errors import (
    GENERIC_SCAN_FAILURE,
    ProviderRequestError,
    PublicScanError,
    normalize_public_cli_line,
    public_error_text,
    public_scan_failure_message,
)
from tads.llm.base import ModelOutputTruncatedError
from tads.llm.providers.mock_provider import MockProvider
from tads.pipeline import FindingParseError, SemanticCorrectionError, run_scan
from tads.progress import ProgressClocks, ScanProgressReporter
from tads.schemas.report import Report

AUTH = "Authorization: Bearer sk-live-SECRET123"
API_KEY = "api_key=sk-proj-LEAKME"
PROMPT = "PROMPT_CANARY_xyz_do_not_print"
DOCUMENT = "DOCUMENT_CANARY_abc_do_not_print"
BODY = "RESPONSE_BODY_CANARY_<html>secret</html>"
URL = "https://user:pass@evil.example/path?token=abc#frag"
CAUSE = "CAUSE_CANARY_should_not_print"
REPR_MARK = "REPR_CANARY_should_not_print"
ATTR_MARK = "ATTR_CANARY_should_not_print"
RICH = "[bold red]RICH_CANARY[/bold red]"
ESC = "\x1b[31mESC_CANARY\x1b[0m"
CRLF = "LINE1_CANARY\r\nLINE2_CANARY"

CANARIES = (
    AUTH,
    API_KEY,
    PROMPT,
    DOCUMENT,
    BODY,
    URL,
    CAUSE,
    REPR_MARK,
    ATTR_MARK,
    "sk-live-SECRET123",
    "sk-proj-LEAKME",
    "user:pass",
    "token=abc",
    "RICH_CANARY",
    "ESC_CANARY",
    "LINE1_CANARY",
    "LINE2_CANARY",
)


class _Contaminated(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            f"upstream failed: {AUTH} | {API_KEY} | {PROMPT} | {DOCUMENT} | "
            f"{BODY} | {URL} | {RICH} | {ESC} | {CRLF}"
        )
        self.headers = {"Authorization": AUTH}
        self.response_body = BODY
        self.prompt_leak = PROMPT
        self.document_leak = DOCUMENT
        self.raw = f"misleading raw {BODY} {PROMPT}"
        req = httpx.Request(
            "POST",
            f"https://{URL.split('://', 1)[-1]}",
        )
        self.request = req
        self.response = httpx.Response(
            500,
            request=req,
            content=BODY.encode("utf-8"),
            headers={"Authorization": AUTH},
        )

    def __repr__(self) -> str:
        return (
            f"Contaminated({REPR_MARK!r}, headers={self.headers!r}, "
            f"body={BODY!r}, url={URL!r})"
        )


def _write_doc(path: Path) -> None:
    path.write_text(
        "1. Introduction\nUnix time and year 2038.\n"
        "2. Requirements\nHandle 32-bit time_t carefully.\n",
        encoding="utf-8",
    )


def _invoke_scan(
    tmp_path: Path,
    monkeypatch,
    *,
    boom,
    isatty: bool = False,
    extra_args: list[str] | None = None,
    clocks: ProgressClocks | None = None,
):
    sample = tmp_path / "doc.txt"
    _write_doc(sample)
    out = tmp_path / "out"
    monkeypatch.setattr(MockProvider, "complete", boom)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=isatty, clocks=clocks or kwargs.get("clocks")
        ),
    )
    args = [
        "scan",
        str(sample),
        "--doc-id",
        "ERR1",
        "--provider",
        "mock",
        "-y",
        "--overwrite",
        "-o",
        str(out / "report"),
    ]
    if extra_args:
        args.extend(extra_args)
    return CliRunner().invoke(app, args), out


def _assert_no_canaries(text: str) -> None:
    for canary in CANARIES:
        assert canary not in text, canary


def test_public_error_text_requires_public_scan_error():
    assert (
        public_error_text(ProviderRequestError("provider request failed"))
        == "provider request failed"
    )
    assert public_error_text(RuntimeError("Authorization: Bearer SECRET")) is None
    assert public_error_text(RuntimeError({"secret": "CANARY"})) is None

    class _Noisy(RuntimeError):
        def __str__(self) -> str:
            raise AssertionError("__str__ must not be invoked")

        def __repr__(self) -> str:
            raise AssertionError("__repr__ must not be invoked")

    noisy = _Noisy("visible")
    noisy.headers = {"Authorization": AUTH}
    noisy.__cause__ = ValueError(CAUSE)
    noisy.__context__ = RuntimeError(BODY)
    assert public_error_text(noisy) is None

    class _Empty(PublicScanError):
        pass

    assert public_error_text(_Empty()) is None
    assert public_error_text(_Empty({"not": "a string"})) is None


def test_public_scan_failure_message_helpers():
    assert public_scan_failure_message(RuntimeError(AUTH)) == GENERIC_SCAN_FAILURE
    known = FindingParseError("Finding item 0: invalid field evidence.quote")
    assert public_scan_failure_message(known) == (
        "Scan failed: Finding item 0: invalid field evidence.quote"
    )
    assert public_scan_failure_message(ProviderRequestError()) == GENERIC_SCAN_FAILURE
    assert (
        public_scan_failure_message(ProviderRequestError({"x": 1}))
        == GENERIC_SCAN_FAILURE
    )
    assert "\r" not in normalize_public_cli_line("a\rb\nc")
    assert "\x1b" not in normalize_public_cli_line(ESC)


def test_public_error_text_call_sites_retain_or_fallback():
    """Ollama / repair / UrlSecurityError paths after the PublicScanError gate."""
    from tads.ingest.fetch import IngestError
    from tads.ingest.url_security import UrlSecurityError
    from tads.llm.http import format_shape_error

    # 1) Ollama: ProviderRequestError message remains available.
    pre = ProviderRequestError(
        "ollama HTTP 404 for https://ollama.com/api/chat (non-retryable)"
    )
    assert public_error_text(pre) == pre.args[0]

    # 2) Pipeline second_error: FindingParseError retains; unknown falls back.
    assert (
        public_error_text(FindingParseError("Could not parse JSON object from model response"))
        == "Could not parse JSON object from model response"
    )
    assert (
        public_error_text(RuntimeError("contaminated"))
        or "Could not parse JSON object from model response"
    ) == "Could not parse JSON object from model response"

    # 3) UrlSecurityError is PublicScanError; non-public falls back.
    assert isinstance(UrlSecurityError("Refusing to fetch non-public network destination: localhost"), PublicScanError)
    secured = UrlSecurityError(
        "Refusing to fetch non-public network destination: localhost"
    )
    text = public_error_text(secured) or "Remote URL rejected"
    assert text == "Refusing to fetch non-public network destination: localhost"
    assert IngestError(text)
    fallback = public_error_text(ValueError("arbitrary")) or "Remote URL rejected"
    assert fallback == "Remote URL rejected"
    _ = format_shape_error


def test_normalize_public_cli_line_strips_terminal_hazards():
    # CR/LF/TAB and C0
    assert normalize_public_cli_line("a\r\nb\tc\x00d") == "a b c d"
    # ESC CSI and remaining ESC introducers (OSC/DCS neutered)
    assert "\x1b" not in normalize_public_cli_line("x\x1b[31mRED\x1b[0my")
    osc = normalize_public_cli_line("\x1b]8;;https://evil.example\x07click")
    assert "\x1b" not in osc
    assert "\x07" not in osc
    # C1 CSI / ST
    assert "\u009b" not in normalize_public_cli_line("x\u009b31mREDy")
    assert "\u009c" not in normalize_public_cli_line("a\u009cb")
    # Unicode line/paragraph separators (also collapsed as whitespace)
    assert normalize_public_cli_line("a\u2028b\u2029c") == "a b c"
    # Bidi override / isolate
    out = normalize_public_cli_line("a\u202eSECRET\u202cb\u2066iso\u2069")
    assert "\u202e" not in out and "\u202c" not in out
    assert "\u2066" not in out and "\u2069" not in out
    # Ordinary UTF-8 document names preserved
    assert normalize_public_cli_line("café-文档.pdf") == "café-文档.pdf"
    # Rich markup left literal (raw writer; not interpreted)
    assert "[red]x[/red]" in normalize_public_cli_line("keep [red]x[/red] ok")
    # Single public line only
    assert "\n" not in normalize_public_cli_line("one\ntwo\rthree")


def test_cli_does_not_print_cause_or_context_even_if_set(
    tmp_path: Path, monkeypatch
):
    """``raise ... from None`` suppresses display; CLI must not traverse graph."""

    def boom(*_a, **_k):
        try:
            raise ValueError(CAUSE)
        except ValueError as inner:
            # Intentionally leave a cause (not from None) to prove CLI safety.
            raise RuntimeError("outer") from inner

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    blob = result.output or ""
    assert GENERIC_SCAN_FAILURE in blob
    assert CAUSE not in blob
    assert "ValueError" not in blob
    assert "outer" not in blob
    assert blob.count("Scan failed:") == 1
    assert blob.count("Failed after ") == 1


def test_unknown_contaminated_runtime_error_is_generic(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        try:
            raise ValueError(CAUSE)
        except ValueError:
            try:
                raise _Contaminated()
            except _Contaminated:
                raise _Contaminated() from None

    result, out = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    blob = result.output or ""
    assert result.exit_code == 1
    assert blob.count("Scan failed:") == 1
    assert blob.count("Failed after ") == 1
    assert GENERIC_SCAN_FAILURE in blob
    assert "Traceback" not in blob
    _assert_no_canaries(blob)
    assert not (out / "report.json").exists()


def test_unknown_exception_graph_not_traversed(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        try:
            raise ValueError(CAUSE)
        except ValueError as inner:
            err = _Contaminated()
            raise err from inner

    result, _out = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    blob = result.output or ""
    assert GENERIC_SCAN_FAILURE in blob
    assert blob.count("Scan failed:") == 1
    assert blob.count("Failed after ") == 1
    _assert_no_canaries(blob)
    assert "Contaminated(" not in blob
    assert "httpx.Request" not in blob
    assert "httpx.Response" not in blob
    assert ATTR_MARK not in blob


def test_policy_a_message_retained(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        raise FindingParseError("Finding item 0: invalid field evidence.quote")

    result, out = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    assert result.exit_code == 1
    assert (
        "Scan failed: Finding item 0: invalid field evidence.quote" in result.output
    )
    assert "Failed after " in result.output
    assert not (out / "report.json").exists()


def test_duplicate_key_and_invalid_number_remain_controlled(
    tmp_path: Path, monkeypatch
):
    from tads.pipeline.parse_findings import extract_json_object

    with pytest.raises(FindingParseError) as dup:
        extract_json_object('{"findings": [], "findings": []}')
    assert str(dup.value) == "Duplicate JSON object key"
    assert "1e999" not in str(dup.value)

    with pytest.raises(FindingParseError) as num:
        extract_json_object('{"findings": [], "n": 1' + ("0" * 50) + "}")
    assert str(num.value) == "Invalid JSON number"

    def boom_dup(*_a, **_k):
        raise FindingParseError("Duplicate JSON object key", raw="SECRET_MODEL")

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom_dup)
    assert "Scan failed: Duplicate JSON object key" in result.output
    assert "SECRET_MODEL" not in result.output


def test_provider_request_error_retains_allowlisted_message(
    tmp_path: Path, monkeypatch
):
    msg = (
        "openai HTTP 401 for https://api.openai.com/v1/chat/completions "
        "(non-retryable)"
    )

    def boom(*_a, **_k):
        raise ProviderRequestError(msg)

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    assert f"Scan failed: {msg}" in result.output
    assert BODY not in result.output
    assert AUTH not in result.output


def test_json_repair_provider_failure_sanitized(tmp_path: Path, monkeypatch):
    from tads.llm.base import LLMResponse
    from tads.llm.registry import register_provider
    from tads.schemas.cost import TokenUsage

    class _Prov(MockProvider):
        def __init__(self) -> None:
            self.n = 0

        def complete(
            self, messages, *, model=None, max_output_tokens=4096, reasoning_effort=None
        ):
            self.n += 1
            if self.n == 1:
                return LLMResponse(
                    content='Here:\n```json\n{"findings": [\n',
                    usage=TokenUsage(input_tokens=1, output_tokens=1),
                    model="mock-model",
                )
            raise _Contaminated()

    register_provider(_Prov())
    try:
        with pytest.raises(ProviderRequestError) as caught:
            run_scan(
                "1. A\nUnix 2038.\n",
                doc_id="REPAIR1",
                corpus="generic",
                provider="mock",
            )
        err = caught.value
        assert str(err) == "JSON repair request failed"
        assert err.__cause__ is None
        assert err.__context__ is None
        for canary in (AUTH, BODY, PROMPT, DOCUMENT, URL, REPR_MARK):
            assert canary not in str(err)
            assert canary not in repr(err)
    finally:
        register_provider(MockProvider())

    def boom(*_a, **_k):
        raise ProviderRequestError("JSON repair request failed")

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    assert "Scan failed: JSON repair request failed" in result.output
    _assert_no_canaries(result.output or "")


def test_semantic_correction_failure_message_retained_at_cli(
    tmp_path: Path, monkeypatch
):
    def boom(*_a, **_k):
        raise SemanticCorrectionError(
            "Semantic evidence correction failed",
            raw=f"{BODY} {PROMPT} {DOCUMENT}",
        )

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    assert "Scan failed: Semantic evidence correction failed" in result.output
    assert BODY not in result.output
    assert PROMPT not in result.output
    assert DOCUMENT not in result.output
    _assert_no_canaries(result.output or "")


def test_truncation_sanitized_cli(tmp_path: Path, monkeypatch):
    secret = f"TRUNC_RAW {BODY} {PROMPT}"

    def boom(*_a, **_k):
        raise ModelOutputTruncatedError(
            "Model output truncated: completion token limit reached",
            raw=secret,
        )

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    assert "Scan failed: Model output truncated" in result.output
    assert secret not in result.output
    _assert_no_canaries(result.output or "")


def test_rich_markup_and_control_chars_normalized(tmp_path: Path, monkeypatch):
    class MarkupError(PublicScanError):
        pass

    def boom(*_a, **_k):
        raise MarkupError(f"safe field{RICH}{ESC}{CRLF}end")

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom, isatty=False)
    blob = result.output or ""
    assert blob.count("Scan failed:") == 1
    failed = [ln for ln in blob.splitlines() if "Scan failed:" in ln][0]
    assert "\r" not in failed
    assert "\x1b" not in failed
    assert failed.count("\n") == 0
    # Markup left literal (raw writer); must not create extra public lines.
    assert blob.count("Failed after ") == 1


def test_tty_live_cleared_before_sanitized_failure(tmp_path: Path, monkeypatch):
    fake_wall = datetime(2026, 10, 17, 5, 16, 7, 123000, tzinfo=timezone.utc)
    clocks = ProgressClocks(
        wall_now=lambda: fake_wall,
        monotonic=lambda: 1000.0,
    )

    def boom(*_a, **_k):
        raise RuntimeError(AUTH)

    result, _ = _invoke_scan(
        tmp_path, monkeypatch, boom=boom, isatty=True, clocks=clocks
    )
    blob = result.output or ""
    assert GENERIC_SCAN_FAILURE in blob
    assert blob.endswith("\n")
    assert "\r" in blob
    idx = blob.index("Scan failed:")
    assert "\r" in blob[:idx]
    _assert_no_canaries(blob)


def test_non_tty_failure_has_no_cr_or_ansi(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError(AUTH)

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom, isatty=False)
    blob = result.output or ""
    assert "\r" not in blob
    assert "\033" not in blob
    assert GENERIC_SCAN_FAILURE in blob


def test_save_raw_on_error_for_known_truncation(tmp_path: Path, monkeypatch):
    secret = f"BOUNDED_RAW {BODY}"

    def boom(*_a, **_k):
        raise ModelOutputTruncatedError(
            "Model output truncated: completion token limit reached",
            raw=secret,
        )

    result, out = _invoke_scan(
        tmp_path,
        monkeypatch,
        boom=boom,
        extra_args=["--save-raw-on-error"],
    )
    raw_path = out / "report.raw.txt"
    assert result.exit_code == 1
    assert raw_path.is_file()
    assert secret in raw_path.read_text(encoding="utf-8")
    assert secret not in (result.output or "")
    assert not (out / "report.json").exists()


def test_unknown_exception_raw_attr_not_written(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        raise _Contaminated()

    result, out = _invoke_scan(
        tmp_path,
        monkeypatch,
        boom=boom,
        extra_args=["--save-raw-on-error"],
    )
    assert result.exit_code == 1
    assert GENERIC_SCAN_FAILURE in result.output
    assert not (out / "report.raw.txt").exists()
    assert not (out / "report.json").exists()
    _assert_no_canaries(result.output or "")


def test_keyboard_interrupt_exit_130(tmp_path: Path, monkeypatch):
    def boom(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr("tads.cli.run_scan", boom)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    sample = tmp_path / "doc.txt"
    _write_doc(sample)
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "INTR",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(tmp_path / "intr"),
        ],
    )
    assert result.exit_code == 130
    assert "Interrupted after " in result.output
    assert GENERIC_SCAN_FAILURE not in result.output
    assert "Traceback" not in result.output


def test_explicit_abort_distinct(tmp_path: Path, monkeypatch):
    sample = tmp_path / "doc.txt"
    _write_doc(sample)
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "ABORT",
            "--provider",
            "mock",
            "--overwrite",
            "-o",
            str(tmp_path / "ab"),
        ],
        input="n\n",
    )
    assert result.exit_code == 1
    assert "Aborted." in result.output
    assert GENERIC_SCAN_FAILURE not in result.output
    assert "Failed after " in result.output


def test_refusal_and_shape_errors_retain_allowlisted_text(
    tmp_path: Path, monkeypatch
):
    from tads.llm.http import (
        format_provider_output_rejected,
        format_shape_error,
        format_unexpected_output_type,
    )

    for msg in (
        format_provider_output_rejected("OpenAI"),
        format_unexpected_output_type("OpenAI"),
        format_shape_error("Anthropic"),
    ):
        captured = msg

        def boom(*_a, _captured=captured, **_k):
            raise ProviderRequestError(_captured)

        result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
        assert f"Scan failed: {msg}" in result.output
        assert GENERIC_SCAN_FAILURE not in result.output
        _assert_no_canaries(result.output or "")


def test_structured_output_unsupported_message_retained(
    tmp_path: Path, monkeypatch
):
    from tads.llm.http import StructuredOutputNotSupportedError

    msg = (
        "openai HTTP 400 for https://api.openai.com/v1/chat/completions "
        "(non-retryable); native structured output unsupported"
    )

    def boom(*_a, **_k):
        raise StructuredOutputNotSupportedError(msg)

    result, _ = _invoke_scan(tmp_path, monkeypatch, boom=boom)
    assert f"Scan failed: {msg}" in result.output
    _assert_no_canaries(result.output or "")


def test_success_unchanged_schema(tmp_path: Path, monkeypatch):
    sample = tmp_path / "doc.txt"
    _write_doc(sample)
    out = tmp_path / "ok"
    monkeypatch.setattr(
        "tads.cli.ScanProgressReporter",
        lambda stream=None, **kwargs: ScanProgressReporter(
            stream=stream, isatty=False
        ),
    )
    result = CliRunner().invoke(
        app,
        [
            "scan",
            str(sample),
            "--doc-id",
            "OK1",
            "--provider",
            "mock",
            "-y",
            "--overwrite",
            "-o",
            str(out / "ok"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Completed successfully in " in result.output
    assert GENERIC_SCAN_FAILURE not in result.output
    report = Report.model_validate(
        json.loads((out / "ok.json").read_text(encoding="utf-8"))
    )
    assert report.schema_version == "0.5.0"
    dumped = report.model_dump(mode="json")
    assert "console_elapsed" not in dumped
