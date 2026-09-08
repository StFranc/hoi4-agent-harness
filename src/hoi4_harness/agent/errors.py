"""Classifying provider failures.

A campaign is hundreds of model calls over hours. Some of them will fail, and the
two kinds of failure want opposite responses:

* **transient** -- a rate limit, a timeout, a 500. The next call will probably
  work. Skip this turn, let the reflex layer play it, carry on.
* **fatal** -- a bad key, a rejected tool schema, a model that does not exist.
  Every subsequent call fails identically, so retrying for six hours produces a
  long transcript of nothing. Stop and say why.

Classification cannot import vendor SDKs (the core imports none), so it works
from the exception's HTTP status where there is one and its class name where
there is not. Anything unrecognised is treated as transient, because a wrong
"transient" costs one turn while a wrong "fatal" costs the run -- but consecutive
transients are counted, so an unrecognised permanent failure still terminates.
"""

from __future__ import annotations

TRANSIENT_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
FATAL_STATUS = {400, 401, 403, 404, 405, 413, 422}

TRANSIENT_NAMES = (
    "ratelimit",
    "timeout",
    "connection",
    "apiconnection",
    "serviceunavailable",
    "overloaded",
    "internalserver",
    "temporarilyunavailable",
    "remoteprotocol",
)
FATAL_NAMES = (
    "authentication",
    "permissiondenied",
    "notfound",
    "badrequest",
    "invalidrequest",
    "unprocessable",
    "modelnotfound",
    "unsupported",
)


def status_of(exc: BaseException) -> int | None:
    """Best-effort HTTP status from an SDK exception, without importing any SDK."""
    for attribute in ("status_code", "http_status", "code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int) and 100 <= value < 600:
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def classify(exc: BaseException) -> str:
    """``"transient"`` or ``"fatal"``."""
    status = status_of(exc)
    if status in FATAL_STATUS:
        return "fatal"
    if status in TRANSIENT_STATUS or (status is not None and status >= 500):
        return "transient"

    name = type(exc).__name__.lower()
    if any(marker in name for marker in FATAL_NAMES):
        return "fatal"
    if any(marker in name for marker in TRANSIENT_NAMES):
        return "transient"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "transient"

    text = str(exc).lower()
    if "rate limit" in text or "overloaded" in text or "timed out" in text:
        return "transient"
    if "api key" in text or "unauthorized" in text or "invalid_request" in text:
        return "fatal"
    return "transient"


def describe(exc: BaseException) -> str:
    status = status_of(exc)
    prefix = f"HTTP {status}: " if status else ""
    return f"{prefix}{type(exc).__name__}: {exc}"[:400]
