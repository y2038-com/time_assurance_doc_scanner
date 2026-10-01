# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Benchmark --skip-existing resume and Ctrl+C interruption (no live providers)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"


def _load_bench_module():
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "run_bench_scans",
        _SCRIPTS / "run_bench_scans.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


bench = _load_bench_module()

CPYTHON_LIMIT = "4300 digits"
CANARY = "CANARY_BENCH_RESUME_xyz"


class _FakeProc:
    def __init__(self, *, wait_result: int = 0, raise_interrupt: bool = False):
        self.wait_result = wait_result
        self.raise_interrupt = raise_interrupt
        self.terminated = False
        self.killed = False
        self._code: int | None = None

    def poll(self) -> int | None:
        return self._code

    def wait(self, timeout: float | None = None) -> int:
        _ = timeout
        if self.raise_interrupt and not self.terminated and not self.killed:
            raise KeyboardInterrupt
        if self._code is None:
            self._code = -2 if (self.terminated or self.killed) else self.wait_result
        return self._code

    def terminate(self) -> None:
        self.terminated = True
        self._code = -2

    def kill(self) -> None:
        self.killed = True
        self._code = -9


def _job(tmp_path: Path, *, name: str = "RFC868", tag: str | None = None) -> object:
    outputs = tmp_path / "out dir"
    outputs.mkdir(parents=True, exist_ok=True)
    prefix = bench._output_prefix(outputs, name, "gemini", "gemini-3.6-flash", tag)
    return bench.BenchJob(
        bench_id="02",
        doc_id=name,
        corpus="ietf",
        source=tmp_path / "RFC868.txt",
        provider="gemini",
        model="gemini-3.6-flash",
        output_prefix=prefix,
    )


def _report_payload(job, *, schema: str = "0.3.0", **run_overrides) -> dict:
    run = {
        "scanner_version": "0.6.0rc3",
        "prompt_framework_version": "0.8.1",
        "provider": job.provider,
        "model": job.model,
    }
    run.update(run_overrides)
    return {
        "schema_version": schema,
        "document": {"corpus": job.corpus, "doc_id": job.doc_id},
        "run": run,
        "findings": [],
    }


def _write_json(job, payload: dict) -> Path:
    path = bench.artifact_paths(job.output_prefix).json_path
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return path


def _write_md(job, text: str = "existing markdown\n") -> Path:
    path = bench.artifact_paths(job.output_prefix).md_path
    path.write_text(text, encoding="utf-8")
    return path


def _stat(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def _run(jobs, tmp_path: Path, **kwargs) -> int:
    options = dict(
        tads="tads",
        skip_existing=True,
        continue_on_error=False,
        plan_only=False,
        dry_run=False,
        max_cost_usd=None,
        max_sections=None,
        max_input_tokens=None,
        force_sections=False,
        max_output_tokens=None,
        outputs_dir=tmp_path / "out dir",
    )
    options.update(kwargs)
    return bench.run_jobs(jobs, **options)


def test_dotted_prefix_json_and_md_are_skipped_without_scan(tmp_path: Path, monkeypatch):
    job = _job(tmp_path, tag="0.6.0rc3-post-width-fix")
    json_path = _write_json(job, _report_payload(job))
    md_path = _write_md(job)
    prefix = job.output_prefix
    assert not prefix.exists()
    json_before, json_mtime = _stat(json_path)
    md_before, md_mtime = _stat(md_path)
    calls: list[list[str]] = []

    def _boom(cmd: list[str]):
        calls.append(cmd)
        raise AssertionError("scan subprocess must not run")

    monkeypatch.setattr(bench, "_popen", _boom)
    code = _run([job], tmp_path)
    assert code == 0
    assert calls == []
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before
    assert json_path.stat().st_mtime_ns == json_mtime
    assert md_path.stat().st_mtime_ns == md_mtime


def test_json_only_regenerates_markdown_without_scan(tmp_path: Path, monkeypatch):
    job = _job(tmp_path, tag="0.6.0rc3-post-width-fix")
    json_path = _write_json(job, _report_payload(job))
    md_path = bench.artifact_paths(job.output_prefix).md_path
    json_before, json_mtime = _stat(json_path)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    assert code == 0
    assert json_path.read_bytes() == json_before
    assert json_path.stat().st_mtime_ns == json_mtime
    assert md_path.is_file()
    text = md_path.read_text(encoding="utf-8")
    assert "Time Assurance Scan Report" in text
    assert job.doc_id in text


def test_markdown_only_is_incomplete_without_scan(tmp_path: Path, monkeypatch):
    job = _job(tmp_path)
    md_path = _write_md(job, "keep me\n")
    before, mtime = _stat(md_path)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    assert code == 1
    assert md_path.read_bytes() == before
    assert md_path.stat().st_mtime_ns == mtime


def test_malformed_json_fails_closed_without_decoder_text(
    tmp_path: Path, monkeypatch, capsys
):
    job = _job(tmp_path)
    json_path = bench.artifact_paths(job.output_prefix).json_path
    huge = "1" * 41
    json_path.write_text('{"schema_version": ' + huge + "}\n", encoding="utf-8")
    md_path = _write_md(job)
    json_before, json_mtime = _stat(json_path)
    md_before, md_mtime = _stat(md_path)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    captured = capsys.readouterr()
    assert code == 1
    blob = captured.out + captured.err
    assert "invalid report" in blob
    assert CPYTHON_LIMIT not in blob
    assert huge not in blob
    assert CANARY not in blob
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before
    assert json_path.stat().st_mtime_ns == json_mtime
    assert md_path.stat().st_mtime_ns == md_mtime


def _mocked_report(job, **overrides):
    from tads.schemas.report import DocumentIdentity, Report, RunMetadata

    data = dict(
        schema_version="loader-accepted",
        document=DocumentIdentity(corpus=job.corpus, doc_id=job.doc_id),
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider=job.provider,
            model=job.model,
        ),
        findings=[],
    )
    data.update(overrides)
    return Report(**data)


def test_mocked_loader_acceptance_skips_without_schema_allowlist(
    tmp_path: Path, monkeypatch
):
    """Runner skip path uses the canonical loader result, not a version tuple."""
    job = _job(tmp_path)
    json_path = _write_json(job, _report_payload(job))
    md_path = _write_md(job)
    json_before, json_mtime = _stat(json_path)
    md_before, md_mtime = _stat(md_path)
    accepted = _mocked_report(job)
    monkeypatch.setattr(bench, "load_report_json", lambda _path: accepted)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    assert _run([job], tmp_path) == 0
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before
    assert json_path.stat().st_mtime_ns == json_mtime
    assert md_path.stat().st_mtime_ns == md_mtime


def test_mocked_loader_acceptance_still_applies_metadata_matching(
    tmp_path: Path, monkeypatch
):
    from tads.schemas.report import RunMetadata

    job = _job(tmp_path)
    json_path = _write_json(job, _report_payload(job))
    md_path = _write_md(job)
    json_before = json_path.read_bytes()
    md_before = md_path.read_bytes()
    accepted = _mocked_report(
        job,
        run=RunMetadata(
            scanner_version="0.6.0rc3",
            provider=job.provider,
            model="other-model",
        ),
    )
    monkeypatch.setattr(bench, "load_report_json", lambda _path: accepted)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    assert code == 1
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before


def test_loader_rejection_fails_closed_without_exception_text(
    tmp_path: Path, monkeypatch, capsys
):
    from tads.jsonutil import JsonLoadError

    job = _job(tmp_path)
    json_path = _write_json(job, _report_payload(job))
    md_path = _write_md(job)
    json_before, json_mtime = _stat(json_path)
    md_before, md_mtime = _stat(md_path)

    def _reject(_path):
        raise JsonLoadError(CANARY)

    monkeypatch.setattr(bench, "load_report_json", _reject)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert code == 1
    assert "invalid report" in blob
    assert CANARY not in blob
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before
    assert json_path.stat().st_mtime_ns == json_mtime
    assert md_path.stat().st_mtime_ns == md_mtime


def test_markdown_symlink_is_not_followed_or_replaced(tmp_path: Path, monkeypatch):
    job = _job(tmp_path)
    _write_json(job, _report_payload(job))
    target = tmp_path / "outside.md"
    target.write_text("secret-target\n", encoding="utf-8")
    md_path = bench.artifact_paths(job.output_prefix).md_path
    try:
        md_path.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are not available on this platform")
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    assert code == 1
    assert md_path.is_symlink()
    assert target.read_text(encoding="utf-8") == "secret-target\n"
    assert bench.classify_existing(job).action == "invalid"


def test_metadata_mismatch_fails_closed(tmp_path: Path, monkeypatch):
    job = _job(tmp_path)
    payload = _report_payload(job)
    payload["run"]["model"] = "other-model"
    json_path = _write_json(job, payload)
    md_path = _write_md(job)
    json_before = json_path.read_bytes()
    md_before = md_path.read_bytes()
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    code = _run([job], tmp_path)
    assert code == 1
    assert json_path.read_bytes() == json_before
    assert md_path.read_bytes() == md_before


def test_raw_txt_only_is_not_complete(tmp_path: Path, monkeypatch):
    job = _job(tmp_path)
    raw = Path(f"{job.output_prefix}.raw.txt")
    raw.write_text("not a report\n", encoding="utf-8")
    calls: list[list[str]] = []

    def _popen(cmd: list[str]) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc(wait_result=0)

    monkeypatch.setattr(bench, "_popen", _popen)
    code = _run([job], tmp_path)
    assert code == 0
    assert len(calls) == 1
    assert calls[0][1] == "scan"


def test_other_tag_files_do_not_satisfy_current_job(tmp_path: Path, monkeypatch):
    current = _job(tmp_path, tag="current-tag")
    other = _job(tmp_path, tag="other-tag")
    _write_json(other, _report_payload(other))
    _write_md(other)
    calls: list[list[str]] = []

    def _popen(cmd: list[str]) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc(wait_result=0)

    monkeypatch.setattr(bench, "_popen", _popen)
    code = _run([current], tmp_path)
    assert code == 0
    assert len(calls) == 1


def test_missing_artifacts_invoke_scan_once(tmp_path: Path, monkeypatch):
    job = _job(tmp_path)
    calls: list[list[str]] = []

    def _popen(cmd: list[str]) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc(wait_result=0)

    monkeypatch.setattr(bench, "_popen", _popen)
    code = _run([job], tmp_path)
    assert code == 0
    assert len(calls) == 1
    assert "-o" in calls[0]
    assert str(job.output_prefix) in calls[0]


def test_continue_on_error_after_invalid_existing(tmp_path: Path, monkeypatch, capsys):
    bad = _job(tmp_path, name="RFC868")
    good = bench.BenchJob(
        bench_id="01",
        doc_id="RFC5905",
        corpus="ietf",
        source=tmp_path / "RFC5905.txt",
        provider="gemini",
        model="gemini-3.6-flash",
        output_prefix=bench._output_prefix(
            tmp_path / "out dir", "RFC5905", "gemini", "gemini-3.6-flash", None
        ),
    )
    _write_json(bad, {"not": "a report"})
    calls: list[list[str]] = []

    def _popen(cmd: list[str]) -> _FakeProc:
        calls.append(cmd)
        return _FakeProc(wait_result=0)

    monkeypatch.setattr(bench, "_popen", _popen)
    code = _run([bad, good], tmp_path, continue_on_error=True)
    captured = capsys.readouterr()
    assert code == 1
    assert len(calls) == 1
    assert "RFC5905" in " ".join(calls[0])
    assert "1 executed" in captured.out
    assert "1 incomplete/invalid" in captured.out


def test_keyboard_interrupt_stops_child_and_skips_later_jobs(
    tmp_path: Path, monkeypatch, capsys
):
    first = _job(tmp_path, name="RFC868")
    second = bench.BenchJob(
        bench_id="01",
        doc_id="RFC5905",
        corpus="ietf",
        source=tmp_path / "RFC5905.txt",
        provider="gemini",
        model="gemini-3.6-flash",
        output_prefix=bench._output_prefix(
            tmp_path / "out dir", "RFC5905", "gemini", "gemini-3.6-flash", None
        ),
    )
    procs: list[_FakeProc] = []

    def _popen(cmd: list[str]) -> _FakeProc:
        proc = _FakeProc(raise_interrupt=True)
        procs.append(proc)
        return proc

    monkeypatch.setattr(bench, "_popen", _popen)
    code = _run([first, second], tmp_path)
    captured = capsys.readouterr()
    blob = captured.out + captured.err
    assert code == 130
    assert len(procs) == 1
    assert procs[0].terminated is True
    assert "Interrupted." in captured.err
    assert "Traceback" not in blob
    assert "KeyboardInterrupt" not in blob
    assert "interrupted" in captured.out
    assert "0 executed" in captured.out


def test_paths_with_spaces_and_punctuation(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "reports (v1)"
    outputs.mkdir()
    prefix = outputs / "RFC 868.v1"
    job = bench.BenchJob(
        bench_id="02",
        doc_id="RFC868",
        corpus="ietf",
        source=tmp_path / "RFC868.txt",
        provider="gemini",
        model="gemini-3.6-flash",
        output_prefix=prefix,
    )
    paths = bench.artifact_paths(prefix)
    assert paths.json_path == outputs / "RFC 868.v1.json"
    assert paths.md_path == outputs / "RFC 868.v1.md"
    assert paths.json_path != prefix.with_suffix(".json")
    _write_json(job, _report_payload(job))
    _write_md(job)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    assert _run([job], tmp_path, outputs_dir=outputs) == 0


def test_legacy_schema_report_skips_when_identity_matches(
    tmp_path: Path, monkeypatch
):
    job = _job(tmp_path)
    payload = _report_payload(job, schema="0.1.0")
    payload["run"].pop("provider")
    payload["run"].pop("model")
    _write_json(job, payload)
    _write_md(job)
    monkeypatch.setattr(
        bench,
        "_popen",
        lambda cmd: (_ for _ in ()).throw(AssertionError("scan must not run")),
    )
    assert _run([job], tmp_path) == 0


def test_classify_does_not_treat_bare_prefix_as_complete(tmp_path: Path):
    job = _job(tmp_path, tag="0.6.0rc3-post-width-fix")
    job.output_prefix.write_text("not a report", encoding="utf-8")
    _write_json(job, _report_payload(job))
    _write_md(job)
    assert bench.classify_existing(job).action == "skip"
    assert job.output_prefix.is_file()
