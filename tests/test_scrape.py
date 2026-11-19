"""Tests for week01_http.scrape (fixed HTML, no network)."""
import json

import pytest

from week01_http.client import create_client
from week01_http.scrape import BASE_URL, ScrapeError, parse_quotes, save_json, scrape_pages


def quote_html(text, author, goodreads=True):
    link = (f'<a href="http://goodreads.com/author/show/1.{author}">(Goodreads page)</a>'
            if goodreads else "")
    return f"""
    <div class="quote">
        <span class="text">{text}</span>
        <span>by <small class="author">{author}</small>
        <a href="/author/{author}">(about)</a> {link}</span>
        <div class="tags"><a class="tag" href="/tag/x/">x</a></div>
    </div>"""


def page_html(*quotes):
    return "<html><body>" + "".join(quotes) + "</body></html>"


def test_parse_quotes_keeps_fields_together():
    html = page_html(quote_html("“One”", "Ann"), quote_html("“Two”", "Bob"))
    assert parse_quotes(html) == [
        {"text": "“One”", "author": "Ann", "goodreads_url": "http://goodreads.com/author/show/1.Ann"},
        {"text": "“Two”", "author": "Bob", "goodreads_url": "http://goodreads.com/author/show/1.Bob"},
    ]


def test_parse_quotes_ignores_about_and_tag_links():
    [record] = parse_quotes(page_html(quote_html("“One”", "Ann")))
    assert "goodreads.com" in record["goodreads_url"]


def test_missing_goodreads_link_means_not_logged_in():
    html = page_html(quote_html("“One”", "Ann"), quote_html("“Two”", "Bob", goodreads=False))
    with pytest.raises(ScrapeError, match="'Bob'"):
        parse_quotes(html)


def test_scrape_pages_fetches_each_page_in_order(httpx_mock):
    for n in (1, 2, 3):
        httpx_mock.add_response(url=f"{BASE_URL}/page/{n}/",
                                text=page_html(quote_html(f"“Q{n}”", f"A{n}")))
    with create_client() as client:
        quotes = scrape_pages(client, pages=3)
    assert [q["text"] for q in quotes] == ["“Q1”", "“Q2”", "“Q3”"]


def test_save_json_round_trips_unicode(tmp_path):
    records = [{"text": "“Olé”", "author": "Ann", "goodreads_url": "http://goodreads.com/x"}]
    path = tmp_path / "quotes.json"
    save_json(records, path)
    assert json.loads(path.read_text(encoding="utf-8")) == records
    assert "“Olé”" in path.read_text(encoding="utf-8")   # not \u-escaped
