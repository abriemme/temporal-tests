"""Unit tests for build-id resolution (config.get_build_id)."""

from __future__ import annotations

import subprocess

import app.config as config
from app.config import get_build_id


def test_get_build_id_prefers_git_sha_env(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SHA", "  deadbeef\n")
    # Should never shell out when the env var is set.
    monkeypatch.setattr(
        config.subprocess,
        "check_output",
        lambda *a, **kw: pytest_fail_if_called(),
    )
    assert get_build_id() == "deadbeef"


def test_get_build_id_falls_back_to_git_rev_parse(monkeypatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setattr(
        config.subprocess,
        "check_output",
        lambda *a, **kw: b"abc123\n",
    )
    assert get_build_id() == "abc123"


def test_get_build_id_unknown_without_repo(monkeypatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)

    def boom(*a, **kw):
        raise FileNotFoundError("git not installed")

    monkeypatch.setattr(config.subprocess, "check_output", boom)
    assert get_build_id() == "unknown"


def test_get_build_id_unknown_on_git_error(monkeypatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)

    def boom(*a, **kw):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(config.subprocess, "check_output", boom)
    assert get_build_id() == "unknown"


def pytest_fail_if_called():  # pragma: no cover - guard helper
    raise AssertionError("subprocess.check_output should not be called")
