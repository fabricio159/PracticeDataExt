"""Tests for week01_http.login (fake responses, plus one opt-in live test)."""
from contextlib import closing
from urllib.parse import parse_qs

import pytest

from week01_http.client import create_client, get
from week01_http.login import URL, LoginError, is_logged_in, login

LOGIN_FORM = '<form><input type="hidden" name="csrf_token" value="tok123"></form>'
LOGGED_IN_PAGE = '<a href="/logout">Logout</a>'
LOGIN_FAILED_PAGE = "<p>Error while logging in: please, provide your username.</p>" + LOGIN_FORM


def test_login_posts_token_and_returns_client(httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, text=LOGIN_FORM)
    httpx_mock.add_response(method="POST", url=URL, text=LOGGED_IN_PAGE)

    with closing(login(username="fabricio", password="anything")) as client:
        assert not client.is_closed

    post_request = httpx_mock.get_requests(method="POST")[0]
    form = parse_qs(post_request.content.decode())
    assert form == {"csrf_token": ["tok123"], "username": ["fabricio"], "password": ["anything"]}


def test_login_without_logout_raises(httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, text=LOGIN_FORM)
    httpx_mock.add_response(method="POST", url=URL, text=LOGIN_FAILED_PAGE)

    with pytest.raises(LoginError, match="no 'Logout' link"):
        login()


def test_missing_csrf_token_raises(httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, text="<html>maintenance</html>")

    with pytest.raises(LoginError, match="no csrf_token field"):
        login()


def test_failed_login_leaves_callers_client_open(httpx_mock):
    httpx_mock.add_response(method="GET", url=URL, text="<html></html>")
    with create_client() as client:
        with pytest.raises(LoginError):
            login(client=client)
        assert not client.is_closed             # we passed it in, so we still own it


@pytest.mark.network
def test_login_live_site():
    """Real request to quotes.toscrape.com. Run with: pytest -m network"""
    with closing(login(username="fabricio", password="anything")) as client:
        assert is_logged_in(get(client, "https://quotes.toscrape.com/"))
