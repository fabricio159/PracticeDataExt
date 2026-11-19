"""Tests for week01_http.status_probe (fake httpbin, no network, no sleeping)."""
import logging

import pytest

from week01_http import client as client_module
from week01_http.status_probe import main

BASE = "https://httpbin.test"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda seconds: None)


@pytest.fixture
def fake_httpbin(httpx_mock):
    httpx_mock.add_response(url=f"{BASE}/status/200", text="")
    httpx_mock.add_response(url=f"{BASE}/status/301", status_code=301,
                            headers={"Location": f"{BASE}/get"})
    httpx_mock.add_response(url=f"{BASE}/get", json={"url": f"{BASE}/get"})
    httpx_mock.add_response(url=f"{BASE}/status/404", status_code=404)
    httpx_mock.add_response(url=f"{BASE}/status/429", status_code=429, is_reusable=True)
    httpx_mock.add_response(url=f"{BASE}/status/503", status_code=503, is_reusable=True)
    return httpx_mock


def test_each_status_gets_the_expected_action(fake_httpbin):
    assert main(base_url=BASE) == {
        200: "parsed",
        301: "followed",
        404: "skipped",
        429: "gave up",
        503: "gave up",
    }


def test_retryable_codes_use_all_attempts_others_use_one(fake_httpbin):
    main(base_url=BASE, max_attempts=3)
    hits = [str(r.url) for r in fake_httpbin.get_requests()]
    assert hits.count(f"{BASE}/status/429") == 3
    assert hits.count(f"{BASE}/status/503") == 3
    assert hits.count(f"{BASE}/status/404") == 1
    assert hits.count(f"{BASE}/status/200") == 1


def test_log_messages_describe_the_actions(fake_httpbin, caplog):
    with caplog.at_level(logging.INFO, logger="week01_http.status_probe"):
        main(base_url=BASE)
    text = caplog.text
    assert "status/200: parsed HTTP 200 as text" in text
    assert f"status/301: followed 301 to {BASE}/get (HTTP 200)" in text
    assert "status/404: skipped HTTP 404" in text
    assert "status/429: retried 2 time(s), gave up (last: HTTP 429)" in text


def test_success_after_retry_is_reported(httpx_mock, caplog):
    httpx_mock.add_response(url=f"{BASE}/status/503", status_code=503)
    httpx_mock.add_response(url=f"{BASE}/status/503", text="ok")
    with caplog.at_level(logging.INFO, logger="week01_http.status_probe"):
        assert main(base_url=BASE, codes=(503,)) == {503: "parsed"}
    assert "retried 1x, then parsed HTTP 200" in caplog.text
