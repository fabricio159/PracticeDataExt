"""Scrape logged-in quotes pages."""
from parsel import Selector
import json

# div span[class="text"]::text
def scrap_page_element(page, css_element):
    selector_text = Selector(page.text).css(css_element).getall()
    return selector_text

def generate_json(page, *args):
    content = {
        "hi": "hello",
        "test": "API"
    }
    with open("quotes.json", "w", encoding="utf-8") as f:
        json.dump(content, f, indent=2, ensure_ascii=False)