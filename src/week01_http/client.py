"""Client factory: headers, timeouts, http2, fetch() with retries."""

import logging
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Browser-like headers (a current desktop Chrome). Accept-Encoding is left to
# httpx so it only advertises encodings it can actually decode.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

RETRYABLE = {429, 500, 502, 503, 504}

# Methods that are safe to repeat: sending them twice has the same effect as once.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}

MAX_WAIT = 60  # seconds; cap for both backoff and Retry-After


class FetchError(RuntimeError):
    """Raised when every attempt failed. Carries the last response, if any."""

    def __init__(self, message: str, last_response: httpx.Response | None = None):
        super().__init__(message)
        self.last_response = last_response


def create_client(**kwargs: Any) -> httpx.Client:
    """Build an httpx.Client. Any `headers` passed are merged on top of DEFAULT_HEADERS;
    other keyword arguments override the defaults below."""
    headers = {**DEFAULT_HEADERS, **kwargs.pop("headers", {})}
    options: dict[str, Any] = {
        "headers": headers,
        "timeout": httpx.Timeout(10, connect=5),
        "follow_redirects": True,
        "http2": True,
    }
    options.update(kwargs)
    return httpx.Client(**options)


def _retry_after_seconds(value: str | None) -> float | None:
    """Parse a Retry-After header: either seconds ("120") or an HTTP date."""
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


def fetch(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    json_body: Any = None,
    max_attempts: int = 3,
    retry_unsafe: bool = False,
) -> httpx.Response:
    """Send a request, retrying network errors and RETRYABLE status codes.

    body          -- dict sent as form data (HTML forms, e.g. a login)
    json_body     -- dict/list sent as JSON (most APIs)
    params        -- query-string values, e.g. {"page": 2}
    headers       -- extra headers for this request only (added to the client's)
    max_attempts  -- total tries, including the first one

    Unsafe methods (POST, PATCH) are tried only once unless retry_unsafe=True,
    because repeating them can perform the action twice.

    Non-retryable responses (2xx, 3xx, 4xx other than 429) are returned as-is.
    Raises FetchError when every attempt failed.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")
    method = method.upper()
    if method not in SAFE_METHODS and not retry_unsafe:
        max_attempts = 1

    last_response: httpx.Response | None = None
    last_error: httpx.TransportError | None = None

    for attempt in range(1, max_attempts + 1):
        wait = min(MAX_WAIT, 2**attempt) + random.uniform(0, 1)
        try:
            response = client.request(method, url, params=params, headers=headers,
                                      data=body, json=json_body)
        except httpx.TransportError as e:  # connect/read timeouts, connection errors, ...
            last_error, last_response = e, None
            log.warning("%s %s attempt %d/%d network error: %r",
                        method, url, attempt, max_attempts, e)
        else:
            if response.status_code not in RETRYABLE:
                return response
            last_error, last_response = None, response
            log.warning("%s %s attempt %d/%d got HTTP %d",
                        method, url, attempt, max_attempts, response.status_code)
            retry_after = _retry_after_seconds(response.headers.get("Retry-After"))
            if retry_after is not None:
                wait = min(MAX_WAIT, retry_after)

        if attempt < max_attempts:  # don't sleep after the last attempt
            time.sleep(wait)

    reason = (f"HTTP {last_response.status_code}" if last_response is not None
              else repr(last_error))
    raise FetchError(
        f"giving up on {method} {url} after {max_attempts} attempt(s); last: {reason}",
        last_response=last_response,
    ) from last_error


def get(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
) -> httpx.Response:
    return fetch(client, "GET", url, params=params, headers=headers,
                 max_attempts=max_attempts)


def post(
    client: httpx.Client,
    url: str,
    *,
    body: dict[str, Any] | None = None,
    json_body: Any = None,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    max_attempts: int = 3,
    retry_unsafe: bool = False,
) -> httpx.Response:
    return fetch(client, "POST", url, body=body, json_body=json_body,
                 params=params, headers=headers,
                 max_attempts=max_attempts, retry_unsafe=retry_unsafe)
