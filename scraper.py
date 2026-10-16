from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

TIMEOUT_SECONDS = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ArticleSummarizer/0.1)"}
ALLOWED_SCHEMES = {"http", "https"}

NOISE_TAGS = ["script", "style", "nav", "footer", "aside"]
MIN_PARAGRAPH_CHARS = 40
MIN_ARTICLE_CHARS = 200


class ScraperError(Exception):
    """Raised when the article can't be downloaded or parsed."""


def fetch_html(url: str) -> str:
    if urlparse(url).scheme not in ALLOWED_SCHEMES:
        raise ScraperError("Only http and https URLs are supported")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise ScraperError(f"Could not fetch the page: {exc}") from exc

    # Without a charset in the headers, requests assumes ISO-8859-1,
    # which garbles Cyrillic and symbols like €. Fall back to detection.
    if resp.encoding is None or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding

    return resp.text


def extract_article(html: str) -> tuple[str, str]:
    """Return (title, text) of the article, or raise ScraperError."""
    soup = BeautifulSoup(html, "html.parser")

    title = _extract_title(soup)

    for tag in soup(NOISE_TAGS):
        tag.decompose()

    text = ""
    article = soup.find("article")
    if article:
        text = _paragraphs_text(article)

    # No <article>, or it was (almost) empty: use all paragraphs on the page.
    if len(text) < MIN_ARTICLE_CHARS:
        text = _paragraphs_text(soup.body or soup)

    if len(text) < MIN_ARTICLE_CHARS:
        raise ScraperError(
            "Could not extract article text (page may be paywalled or JS-rendered)"
        )

    return title, text


def _extract_title(soup: BeautifulSoup) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return _clean(h1.get_text(" "))
    if soup.title and soup.title.get_text(strip=True):
        return _clean(soup.title.get_text(" "))
    return ""


def _paragraphs_text(container: Tag) -> str:
    paragraphs = []
    for p in container.find_all("p"):
        text = _clean(p.get_text(" "))
        if len(text) >= MIN_PARAGRAPH_CHARS:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _clean(text: str) -> str:
    return " ".join(text.split())