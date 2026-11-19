"""Tests for week01_http.client (no network: pytest-httpx fakes every response)."""
import httpx
import pytest

from week01_http import client as client_module
from week01_http.client import DEFAULT_HEADERS, FetchError, create_client, fetch, get, post

URL = "https://example.test/items"


@pytest.fixture
def client():
    with create_client() as c:
        yield c


@pytest.fixture
def sleeps(monkeypatch):
    """Record retry sleeps instead of actually waiting."""
    calls = []
    monkeypatch.setattr(client_module.time, "sleep", calls.append)
    return calls


# --- create_client -----------------------------------------------------------

def test_create_client_defaults(client):
    assert isinstance(client, httpx.Client)
    assert client.follow_redirects is True
    assert client.timeout.connect == 5
    assert client.timeout.read == 10
    assert client.headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]


def test_create_client_merges_headers():
    with create_client(headers={"Authorization": "Bearer t"}) as c:
        assert c.headers["Authorization"] == "Bearer t"
        assert c.headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]  # not replaced


# --- fetch: success and body/params/headers ----------------------------------

def test_get_returns_response(client, httpx_mock):
    httpx_mock.add_response(url=URL, text="ok")
    assert get(client, URL).text == "ok"


def test_post_sends_form_body(client, httpx_mock):
    httpx_mock.add_response(method="POST", url=URL)
    post(client, URL, body={"user": "x", "n": 1})
    request = httpx_mock.get_request()
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert request.content == b"user=x&n=1"


def test_post_sends_json_params_and_extra_headers(client, httpx_mock):
    httpx_mock.add_response(method="POST", url=URL + "?page=2")
    post(client, URL, json_body={"a": [1]}, params={"page": 2}, headers={"X-Test": "1"})
    request = httpx_mock.get_request()
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == b'{"a":[1]}'
    assert request.headers["X-Test"] == "1"
    assert request.headers["User-Agent"] == DEFAULT_HEADERS["User-Agent"]


def test_4xx_is_returned_not_retried(client, httpx_mock, sleeps):
    httpx_mock.add_response(url=URL, status_code=404)
    assert get(client, URL).status_code == 404
    assert sleeps == []


# --- fetch: retries ----------------------------------------------------------

def test_retries_503_then_succeeds(client, httpx_mock, sleeps):
    httpx_mock.add_response(url=URL, status_code=503)
    httpx_mock.add_response(url=URL, status_code=200, text="ok")
    assert get(client, URL).text == "ok"
    assert len(httpx_mock.get_requests()) == 2
    assert len(sleeps) == 1


def test_retries_network_error_then_succeeds(client, httpx_mock, sleeps):
    httpx_mock.add_exception(httpx.ConnectTimeout("slow"), url=URL)
    httpx_mock.add_response(url=URL, text="ok")
    assert get(client, URL).text == "ok"


def test_gives_up_after_max_attempts_without_final_sleep(client, httpx_mock, sleeps):
    httpx_mock.add_response(url=URL, status_code=503, is_reusable=True)
    with pytest.raises(FetchError, match=r"after 3 attempt\(s\); last: HTTP 503") as exc:
        get(client, URL, max_attempts=3)
    assert len(httpx_mock.get_requests()) == 3
    assert len(sleeps) == 2                     # between attempts only
    assert exc.value.last_response.status_code == 503


def test_network_failure_is_chained(client, httpx_mock, sleeps):
    httpx_mock.add_exception(httpx.ConnectError("refused"), url=URL, is_reusable=True)
    with pytest.raises(FetchError) as exc:
        get(client, URL, max_attempts=2)
    assert isinstance(exc.value.__cause__, httpx.ConnectError)
    assert exc.value.last_response is None


def test_post_is_not_retried_by_default(client, httpx_mock, sleeps):
    httpx_mock.add_response(method="POST", url=URL, status_code=503)
    with pytest.raises(FetchError):
        post(client, URL, body={"a": "b"})
    assert len(httpx_mock.get_requests()) == 1
    assert sleeps == []


def test_post_retries_when_marked_safe(client, httpx_mock, sleeps):
    httpx_mock.add_response(method="POST", url=URL, status_code=503)
    httpx_mock.add_response(method="POST", url=URL, status_code=200)
    assert post(client, URL, retry_unsafe=True).status_code == 200


def test_retry_after_seconds_is_used_and_capped(client, httpx_mock, sleeps):
    httpx_mock.add_response(url=URL, status_code=429, headers={"Retry-After": "7"})
    httpx_mock.add_response(url=URL, status_code=429, headers={"Retry-After": "3600"})
    httpx_mock.add_response(url=URL, text="ok")
    get(client, URL)
    assert sleeps == [7, client_module.MAX_WAIT]


def test_retry_after_http_date_is_understood(client, httpx_mock, sleeps):
    httpx_mock.add_response(url=URL, status_code=503,
                            headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    httpx_mock.add_response(url=URL, text="ok")
    get(client, URL)
    assert sleeps == [0]                        # date in the past -> retry now


def test_max_attempts_must_be_positive(client):
    with pytest.raises(ValueError):
        fetch(client, "GET", URL, max_attempts=0)
