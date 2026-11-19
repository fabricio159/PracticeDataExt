"""Scrape logged-in quotes pages."""
import json
from contextlib import closing
from pathlib import Path

import httpx
from parsel import Selector

from week01_http.client import get
from week01_http.login import login

BASE_URL = "https://quotes.toscrape.com"

Quote = dict[str, str]  # {"text", "author", "goodreads_url"}


class ScrapeError(RuntimeError):
    """Raised when a page doesn't look like a logged-in quotes page."""


def parse_quotes(html: str) -> list[Quote]:
    """Return one dict per quote on the page: text, author, goodreads_url.

    Each record is read from its own <div class="quote"> block, so the three
    fields always belong to the same quote.
    """
    quotes = []
    for block in Selector(html).css("div.quote"):
        record = {
            "text": block.css("span.text::text").get(),
            "author": block.css("small.author::text").get(),
            "goodreads_url": block.css('a[href*="goodreads.com"]::attr(href)').get(),
        }
        # The Goodreads link is only shown to logged-in users.
        if record["goodreads_url"] is None:
            raise ScrapeError(f"no Goodreads link for {record['author']!r}; not logged in?")
        quotes.append(record)
    return quotes


def scrape_pages(client: httpx.Client, pages: int = 3) -> list[Quote]:
    """Fetch /page/1/ .. /page/<pages>/ with `client` and return all quotes."""
    all_quotes = []
    for page_number in range(1, pages + 1):
        response = get(client, f"{BASE_URL}/page/{page_number}/")
        all_quotes += parse_quotes(response.text)
    return all_quotes


def save_json(records: list[Quote], path: str | Path = "quotes.json") -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    with closing(login()) as client:
        quotes = scrape_pages(client, pages=3)
    save_json(quotes, "quotes.json")
    print(f"saved {len(quotes)} quotes to quotes.json")
