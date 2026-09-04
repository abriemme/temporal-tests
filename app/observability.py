"""Optional Logfire instrumentation for the worker.

Configures Logfire and instruments the pydantic-ai agent (and system metrics).
It is a no-op unless a Logfire token is configured
(``send_to_logfire="if-token-present"``), so tests and CI never emit telemetry
and the worker runs fine without a token.

Set ``LOGFIRE_TOKEN`` (write token) to ship traces/metrics to Logfire.
"""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger(__name__)


def setup_logfire() -> None:
    """Configure Logfire + instrument pydantic-ai. Safe to call unconditionally."""
    try:
        import logfire
    except ImportError:  # pragma: no cover - logfire is a worker-only dep
        log.info("logfire not installed; skipping instrumentation.")
        return

    logfire.configure(
        service_name="ig-to-karakeep",
        # Only ships when a token is present; otherwise a local no-op.
        send_to_logfire="if-token-present",
    )
    # System metrics need the optional extra; degrade gracefully without it.
    with contextlib.suppress(Exception):
        logfire.instrument_system_metrics()
    logfire.instrument_pydantic_ai()
    log.info("Logfire instrumentation enabled (pydantic-ai + system metrics).")
