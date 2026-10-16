from unittest.mock import Mock, patch

import pytest
import requests

from scraper import ScraperError, extract_article, fetch_html

# Three sample paragraphs, each longer than MIN_PARAGRAPH_CHARS (40) and
# together longer than MIN_ARTICLE_CHARS (200), so they count as an article.
PARA_1 = "The central bank held interest rates steady on Thursday, citing persistent inflation in the services sector."
PARA_2 = "Analysts at several banks had expected a cut, and bond yields rose sharply after the announcement."
PARA_3 = "Markets in Europe closed lower, with financial stocks leading the decline across most major exchanges."


def _page(body: str, head: str = "") -> str:
    return f"<html><head>{head}</head><body>{body}</body></html>"


# ---------- extract_article ----------


def test_extracts_title_and_article_paragraphs():
    html = _page(
        f"""
        <nav><p>Home | Markets | Economy | Sign in to your account today</p></nav>
        <h1>  Central bank   holds rates </h1>
        <article>
            <p>{PARA_1}</p>
            <p>{PARA_2}</p>
            <p>{PARA_3}</p>
            <aside><p>Subscribe to our premium newsletter for exclusive content.</p></aside>
            <script>var tracking = "this paragraph should never appear in output";</script>
        </article>
        <footer><p>Copyright 2026 Example Media. All rights reserved worldwide.</p></footer>
        """
    )

    title, text = extract_article(html)

    assert title == "Central bank holds rates"  # whitespace normalised
    assert text == f"{PARA_1}\n\n{PARA_2}\n\n{PARA_3}"
    assert "Subscribe" not in text
    assert "Copyright" not in text
    assert "tracking" not in text


def test_falls_back_to_all_paragraphs_when_no_article_tag():
    html = _page(
        f"<div><p>{PARA_1}</p><p>too short</p><p>{PARA_2}</p><p>{PARA_3}</p></div>"
    )

    _, text = extract_article(html)

    assert text == f"{PARA_1}\n\n{PARA_2}\n\n{PARA_3}"  # "too short" is dropped


def test_title_falls_back_to_title_tag_then_empty():
    body = f"<p>{PARA_1}</p><p>{PARA_2}</p><p>{PARA_3}</p>"

    title, _ = extract_article(_page(body, head="<title>Markets Wrap</title>"))
    assert title == "Markets Wrap"

    title, _ = extract_article(_page(body))
    assert title == ""


def test_raises_when_there_is_too_little_text():
    html = _page(f"<h1>Paywalled</h1><p>{PARA_1}</p>")  # ~100 chars < 200

    with pytest.raises(ScraperError):
        extract_article(html)


# ---------- fetch_html ----------


@pytest.mark.parametrize("url", ["ftp://example.com/a", "file:///etc/passwd", "example.com"])
def test_fetch_html_rejects_non_http_urls(url):
    with patch("scraper.requests.get") as mock_get:
        with pytest.raises(ScraperError):
            fetch_html(url)
    mock_get.assert_not_called()  # rejected before any network call


def test_fetch_html_returns_page_text():
    resp = Mock(text="<html>ok</html>", encoding="utf-8")
    with patch("scraper.requests.get", return_value=resp) as mock_get:
        assert fetch_html("https://example.com/a") == "<html>ok</html>"
    assert mock_get.call_args.kwargs["timeout"] == 10


def test_fetch_html_wraps_network_and_http_errors():
    with patch("scraper.requests.get", side_effect=requests.Timeout("slow")):
        with pytest.raises(ScraperError, match="Could not fetch"):
            fetch_html("https://example.com/a")

    resp = Mock()
    resp.raise_for_status.side_effect = requests.HTTPError("404")
    with patch("scraper.requests.get", return_value=resp):
        with pytest.raises(ScraperError):
            fetch_html("https://example.com/a")


def test_fetch_html_uses_detected_encoding_when_header_says_latin1():
    resp = Mock(text="x", encoding="ISO-8859-1", apparent_encoding="utf-8")
    with patch("scraper.requests.get", return_value=resp):
        fetch_html("https://example.com/a")
    assert resp.encoding == "utf-8"

def test_content_inside_a_page_wide_form_is_kept():
    html = _page(f"<form><p>{PARA_1}</p><p>{PARA_2}</p><p>{PARA_3}</p></form>")

    _, text = extract_article(html)

    assert text == f"{PARA_1}\n\n{PARA_2}\n\n{PARA_3}"