"""Probe httpbin status codes and log the reaction.

For each /status/<code> endpoint, logs what the client did:
parsed, followed (redirects), skipped (non-retryable error), retried, gave up.
"""
import logging
from collections import Counter

import httpx

from week01_http.client import FetchError, create_client, get

BASE_URL = "https://httpbin.org"
CODES = (200, 301, 404, 429, 503)

log = logging.getLogger(__name__)


def parse_body(response: httpx.Response) -> object:
    """JSON if the server says so, otherwise text (may be empty)."""
    if "json" in response.headers.get("content-type", ""):
        return response.json()
    return response.text


def probe(client: httpx.Client, attempts: Counter, url: str, max_attempts: int = 3) -> str:
    """Request `url` and return (and log) the action the client took.

    `attempts` is filled by a request event hook on `client`, so it counts
    every attempt fetch() made, including retries.
    """
    try:
        response = get(client, url, max_attempts=max_attempts)
    except FetchError as e:
        last = e.last_response.status_code if e.last_response is not None else "network error"
        log.error("%s: retried %d time(s), gave up (last: HTTP %s)",
                  url, attempts[url] - 1, last)
        return "gave up"

    retried = f"retried {attempts[url] - 1}x, then " if attempts[url] > 1 else ""

    if response.history:
        hops = " -> ".join(str(r.status_code) for r in response.history)
        log.info("%s: %sfollowed %s to %s (HTTP %d)",
                 url, retried, hops, response.url, response.status_code)

    if response.is_success:
        body = parse_body(response)
        kind = "JSON" if not isinstance(body, str) else "text"
        log.info("%s: %sparsed HTTP %d as %s (%d bytes)",
                 url, retried, response.status_code, kind, len(response.content))
        return "followed" if response.history else "parsed"

    log.warning("%s: %sskipped HTTP %d (not retryable)", url, retried, response.status_code)
    return "skipped"


def main(base_url: str = BASE_URL, codes: tuple[int, ...] = CODES,
         max_attempts: int = 3) -> dict[int, str]:
    """Probe each /status/<code> and return {code: action}."""
    attempts: Counter = Counter()

    def count_attempt(request: httpx.Request) -> None:  # httpx event hook
        attempts[str(request.url)] += 1

    results = {}
    with create_client(event_hooks={"request": [count_attempt]}) as client:
        for code in codes:
            results[code] = probe(client, attempts, f"{base_url}/status/{code}", max_attempts)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # hide httpx's per-request INFO lines
    for code, action in main().items():
        print(f"{code}: {action}")
