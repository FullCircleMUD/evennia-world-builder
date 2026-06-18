# SPDX-License-Identifier: BSD-3-Clause
"""Logging shim for evennia-world-builder.

See docs/logging.md for the design rationale. One public helper:

    wb_log(message, level="INFO")

routes a line into ``world-builder.log`` (co-located with Evennia's
other logs under ``settings.LOG_DIR``) via Evennia's built-in
``evennia.utils.logger.log_file``. The filename is hardcoded and the
format is fixed:

    <ISO-8601 timestamp> [<LEVEL>] <message>

Outside an Evennia engine (the ``wb-validate`` CLI path, or any caller
where Evennia is not bootstrapped), ``wb_log`` is a silent no-op: the
import of ``evennia.utils.logger`` is lazy and an ``ImportError`` is
swallowed. The library deliberately does not fall back to stderr or a
local file in that case.
"""
from datetime import datetime, timezone

_LOG_FILENAME = "world-builder.log"
_VALID_LEVELS = ("INFO", "WARN", "ERROR")


def wb_log(message: str, level: str = "INFO") -> None:
    """Emit one line to ``world-builder.log``.

    ``level`` is coerced to ``INFO`` if not one of ``INFO``/``WARN``/
    ``ERROR``. A log call must never raise into the caller, so unknown
    levels degrade gracefully rather than rejecting.
    """
    try:
        from evennia.utils.logger import log_file
    except ImportError:
        return

    if level not in _VALID_LEVELS:
        level = "INFO"

    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    log_file(f"{timestamp} [{level}] {message}", filename=_LOG_FILENAME)
