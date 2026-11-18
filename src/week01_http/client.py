"""Client factory: headers, timeouts, http2, fetch() with retries."""

import logging
import random
import time

import httpx

log = logging.getLogger("client")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DataExtProject/0.1)",
}

RETRYABLE = {429, 500, 502, 503, 504}

# Methods that are safe to repeat: sending them twice has the same effect as once.
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "PUT", "DELETE"}

MAX_WAIT = 60  # seconds; cap for both backoff and Retry-After


def create_client(**args):
    """Build an httpx.Client. Any `headers` passed are merged on top of DEFAULT_HEADERS."""
    headers = {**DEFAULT_HEADERS, **args.pop("headers", {})}
    options = {
        "headers": headers,
        "timeout": httpx.Timeout(10, connect=5),
        "follow_redirects": True,
        "http2": True,
    }
    options.update(args)
    return httpx.Client(**options)


def fetch(client, method, url, *, params=None, headers=None,
          body=None, json_body=None, max_retries=3, retry_unsafe=False):
    """Send a request, retrying network errors and RETRYABLE status codes.

    body       -- dict sent as form data (HTML forms, e.g. a login)
    json_body  -- dict/list sent as JSON (most APIs)
    params     -- query-string values, e.g. {"page": 2}
    headers    -- extra headers for this request only (added to the client's)

    Unsafe methods (POST, PATCH) are tried only once unless retry_unsafe=True,
    because repeating them can perform the action twice.
    """
    method = method.upper()
    if method not in SAFE_METHODS and not retry_unsafe:
        max_retries = 1

    for attempt in range(1, max_retries + 1):
        wait = min(MAX_WAIT, 2**attempt) + random.uniform(0, 1)
        try:
            r = client.request(method, url, params=params, headers=headers,
                               data=body, json=json_body)
        except httpx.TransportError as e:  # connect/read timeouts, connection errors, ...
            log.warning("%s %s attempt %d network error: %s", method, url, attempt, e)
        else:
            if r.status_code not in RETRYABLE:
                return r
            log.warning("%s %s attempt %d got %d", method, url, attempt, r.status_code)
            retry_after = r.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait = min(MAX_WAIT, int(retry_after))

        if attempt < max_retries:  # don't sleep after the last attempt
            time.sleep(wait)

    raise RuntimeError(f"giving up on {method} {url} after {max_retries} tries")


def get(client, url, *, params=None, headers=None, max_retries=3):
    return fetch(client, "GET", url, params=params, headers=headers,
                 max_retries=max_retries)


def post(client, url, *, body=None, json_body=None, params=None,
         headers=None, max_retries=3, retry_unsafe=False):
    return fetch(client, "POST", url, body=body, json_body=json_body,
                 params=params, headers=headers,
                 max_retries=max_retries, retry_unsafe=retry_unsafe)
