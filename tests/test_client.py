"""Tests for week01_http.client."""
from httpx import Client

from week01_http.client import create_client, get, post
from parsel import Selector
import pytest
from contextlib import closing


@pytest.fixture
def get_client():
    with closing(create_client()) as client:
        yield client

@pytest.fixture
def get_token(get_client):
    page = get(get_client, "https://quotes.toscrape.com/login")
    return Selector(page.text).css("input[name=csrf_token]::attr(value)").get()

def test_get_client(get_client):
    assert isinstance(get_client, Client)

def test_post(get_client, get_token):
    body = {
        "csrf_token": get_token,
        "username": "fabricio",
        "password": "anything",
    }
    response = post(get_client, "https://quotes.toscrape.com/login", body=body)
    assert "Logout" in response.text

