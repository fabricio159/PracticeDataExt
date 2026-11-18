"""CSRF login to quotes.toscrape.com."""
from contextlib import closing

from parsel import Selector
import json
from week01_http.client import create_client, get, post

URL = "https://quotes.toscrape.com/login"


class LoginError(RuntimeError):
    """Raised when the CSRF login flow does not end in a logged-in session."""


def get_csrf_token(client):
    response = get(client, URL)
    token = Selector(response.text).css("input[name=csrf_token]::attr(value)").get()
    if not token:
        raise LoginError(
            f"no csrf_token field on {response.url} (HTTP {response.status_code}); "
            "the login page layout may have changed"
        )
    return token


def post_login_with_token(client, token, username="test", password="test"):
    body = {"csrf_token": token, "username": username, "password": password}
    # Logging in twice is harmless, so retrying this POST is safe.
    return post(client, URL, body=body, retry_unsafe=True)


def is_logged_in(response):
    return "Logout" in response.text


def login(username="test", password="test", client=None):
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

def scrap_page_element(page, css_element):
    selector_text = Selector(page.text).css(css_element).getall()
    return selector_text


def generate_json(*args):
    data = [
        {"quote": q, "author": a, "link": l} for q, a, l in zip(args[0], args[1], args[2])
    ]
    print(data)
    with open("quotes.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    with closing(login()) as client:
        print("logged in; session cookies:", list(client.cookies.keys()))
        page = get(client, "https://quotes.toscrape.com")
        print("home page shows Logout:", is_logged_in(page))


        page1 = get(client, "https://quotes.toscrape.com/login")
        token = Selector(page1.text).css("input[name=csrf_token]::attr(value)").get()
        print(token)
        body = {
            "csrf_token": token,
            "username": "fabricio",
            "password": "anything",
        }
        response = post(client, "https://quotes.toscrape.com/login", body=body)
        print(response)
        # print(scrap_page(response, client=client)[:3])
        scrapped_page_quotes = scrap_page_element(response, 'div span[class="text"]::text')[:3]
        print(scrapped_page_quotes)
        scrapped_page_author = scrap_page_element(response, 'span small[class="author"]::text')[:3]
        print(scrapped_page_author)
        scrapped_page_link = scrap_page_element(response, 'div.quote a[href*="goodreads.com"]::attr(href)')[:3]
        print(scrapped_page_link)
        generate_json(scrapped_page_quotes, scrapped_page_author, scrapped_page_link)
        