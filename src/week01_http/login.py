"""CSRF login to quotes.toscrape.com."""
from contextlib import closing

import httpx
from parsel import Selector

from week01_http.client import create_client, get, post

URL = "https://quotes.toscrape.com/login"


class LoginError(RuntimeError):
    """Raised when the CSRF login flow does not end in a logged-in session."""


def get_csrf_token(client: httpx.Client) -> str:
    response = get(client, URL)
    token = Selector(response.text).css("input[name=csrf_token]::attr(value)").get()
    if not token:
        raise LoginError(
            f"no csrf_token field on {response.url} (HTTP {response.status_code}); "
            "the login page layout may have changed"
        )
    return token


def post_login_with_token(
    client: httpx.Client, token: str, username: str = "test", password: str = "test"
) -> httpx.Response:
    body = {"csrf_token": token, "username": username, "password": password}
    # Logging in twice is harmless, so retrying this POST is safe.
    return post(client, URL, body=body, retry_unsafe=True)


def is_logged_in(response: httpx.Response) -> bool:
    return "Logout" in response.text


def login(
    username: str = "test", password: str = "test", client: httpx.Client | None = None
) -> httpx.Client:
    """Log in with the CSRF flow and return the logged-in client.

    GETs the login page for a CSRF token (the server ties it to a session
    cookie the client stores), POSTs it with the credentials, and checks the
    resulting page for "Logout".

    Pass `client` to log in an existing client; otherwise a new one is
    created. The caller owns the returned client and must close it, e.g.
    `with closing(login()) as client: ...` (a plain `with login()` fails:
    httpx refuses to enter a client that has already sent requests).
    If login fails, a client created here is closed before the error is raised.
    """
    owns_client = client is None
    if owns_client:
        client = create_client()
    try:
        token = get_csrf_token(client)
        response = post_login_with_token(client, token, username, password)
        if not is_logged_in(response):
            raise LoginError(
                f"login as {username!r} failed: no 'Logout' link on {response.url} "
                f"(HTTP {response.status_code}) after posting the CSRF form"
            )
    except BaseException:
        if owns_client:
            client.close()
        raise
    return client


if __name__ == "__main__":
    with closing(login()) as client:
        print("logged in; session cookies:", list(client.cookies.keys()))
        page = get(client, "https://quotes.toscrape.com/")
        print("home page shows Logout:", is_logged_in(page))
