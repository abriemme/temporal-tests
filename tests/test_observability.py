"""Tests for the optional Logfire instrumentation hook."""

from __future__ import annotations

import builtins
import sys
import types

from app.observability import setup_logfire


class _FakeLogfire(types.ModuleType):
    def __init__(self, system_metrics_ok: bool = True) -> None:
        super().__init__("logfire")
        self.calls: list[str] = []
        self._system_metrics_ok = system_metrics_ok

    def configure(self, **kwargs):
        self.calls.append(f"configure:{kwargs['send_to_logfire']}")

    def instrument_system_metrics(self):
        if not self._system_metrics_ok:
            raise RuntimeError("optional extra missing")
        self.calls.append("system_metrics")

    def instrument_pydantic_ai(self):
        self.calls.append("pydantic_ai")


def test_setup_logfire_instruments_when_available(monkeypatch) -> None:
    fake = _FakeLogfire()
    monkeypatch.setitem(sys.modules, "logfire", fake)

    setup_logfire()

    assert fake.calls == [
        "configure:if-token-present",
        "system_metrics",
        "pydantic_ai",
    ]


def test_setup_logfire_survives_missing_system_metrics(monkeypatch) -> None:
    fake = _FakeLogfire(system_metrics_ok=False)
    monkeypatch.setitem(sys.modules, "logfire", fake)

    setup_logfire()

    assert fake.calls == ["configure:if-token-present", "pydantic_ai"]


def test_setup_logfire_is_a_noop_without_logfire(monkeypatch) -> None:
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "logfire":
            raise ImportError("no logfire")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    setup_logfire()  # must not raise
