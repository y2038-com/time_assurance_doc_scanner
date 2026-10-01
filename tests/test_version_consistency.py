# Copyright (c) 2026 Y2038.com LLC
# SPDX-License-Identifier: Apache-2.0

"""Authoritative package-version surfaces must stay aligned."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

from typer.testing import CliRunner

from tads import __version__
from tads.cli import app
from tads.prompts import PROMPT_FRAMEWORK_VERSION
from tads.schemas.report import Report

EXPECTED_PACKAGE = "0.6.0rc3"
EXPECTED_SCHEMA = "0.3.0"
EXPECTED_PROMPT = "0.8.1"
DISTRIBUTION_NAME = "time-assurance-doc-scanner"
ROOT = Path(__file__).resolve().parents[1]


def _project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_authoritative_package_versions_agree():
    project = _project_version()
    installed = metadata.version(DISTRIBUTION_NAME)
    assert project == EXPECTED_PACKAGE
    assert __version__ == EXPECTED_PACKAGE
    assert installed == EXPECTED_PACKAGE
    assert project == __version__ == installed


def test_package_version_is_pep440_rc3():
    assert EXPECTED_PACKAGE == "0.6.0rc3"
    assert EXPECTED_PACKAGE.endswith("rc3")
    assert "-" not in EXPECTED_PACKAGE


def test_cli_and_module_version_match_package():
    runner = CliRunner()
    direct = runner.invoke(app, ["version"])
    assert direct.exit_code == 0, direct.output
    assert direct.output.strip() == EXPECTED_PACKAGE

    module = subprocess.run(
        [sys.executable, "-m", "tads.cli", "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert module.returncode == 0, module.stderr
    assert module.stdout.strip() == EXPECTED_PACKAGE


def test_schema_and_prompt_versions_are_independent():
    assert Report.model_fields["schema_version"].default == EXPECTED_SCHEMA
    assert PROMPT_FRAMEWORK_VERSION == EXPECTED_PROMPT
    assert EXPECTED_PACKAGE != EXPECTED_SCHEMA
    assert EXPECTED_PACKAGE != EXPECTED_PROMPT
    assert _project_version() != EXPECTED_SCHEMA
    assert __version__ != EXPECTED_PROMPT
