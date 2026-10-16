import pytest

import db
from db import get_summary, init_db, normalize_url, save_summary

URL = "https://example.com/markets/rates-held"


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    """Every test gets its own empty database file; the real one is never touched."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    init_db()


# ---------- normalize_url ----------


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://Example.com/a/?utm_source=x&b=2&a=1#frag", "https://example.com/a?a=1&b=2"),
        ("https://example.com/a?fbclid=abc", "https://example.com/a"),
        ("https://example.com/a/", "https://example.com/a"),
        ("  https://example.com/a  ", "https://example.com/a"),
        ("https://example.com/a?q=café", "https://example.com/a?q=caf%C3%A9"),
        ("https://example.com/a?print", "https://example.com/a?print="),
    ],
)
def test_equivalent_urls_normalize_identically(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_blank_query_values_are_kept():
    assert (
        normalize_url("https://example.com/a?print=&id=5")
        == "https://example.com/a?id=5&print="
    )


@pytest.mark.parametrize(
    "a, b",
    [
        ("https://example.com/a", "https://example.com/A"),  # paths are case-sensitive
        ("https://example.com/a?id=1", "https://example.com/a?id=2"),
        ("https://example.com/a", "http://example.com/a"),  # deliberately conservative
    ],
)
def test_different_urls_stay_different(a, b):
    assert normalize_url(a) != normalize_url(b)


def test_normalize_is_idempotent():
    url = "https://Example.com/a/?utm_source=x&b=2#frag"
    assert normalize_url(normalize_url(url)) == normalize_url(url)


# ---------- cache read / write ----------


def test_miss_returns_none():
    assert get_summary(URL) is None


def test_save_then_get_roundtrip():
    save_summary(URL, "Rates held", "- Point one", "llama3.2")

    row = get_summary(URL)

    assert row is not None  # also narrows the type: get_summary returns dict | None
    assert row["url"] == URL
    assert row["title"] == "Rates held"
    assert row["summary"] == "- Point one"
    assert row["model"] == "llama3.2"
    assert row["created_at"]  # filled in by the database


def test_different_spellings_of_a_url_share_one_entry():
    save_summary(URL + "/?utm_source=newsletter#top", "Title", "Summary", "llama3.2")

    assert get_summary(URL) is not None
    assert get_summary(URL + "?fbclid=abc") is not None


def test_stored_url_is_the_normalized_one():
    save_summary("https://Example.com/a/?utm_source=x", "Title", "Summary", "llama3.2")

    row = get_summary("https://example.com/a")

    assert row is not None
    assert row["url"] == "https://example.com/a"


def test_second_save_overwrites_the_first():
    save_summary(URL, "Old title", "Old summary", "llama3.2")
    save_summary(URL, "New title", "New summary", "mistral")

    row = get_summary(URL)

    assert row is not None
    assert (row["title"], row["summary"], row["model"]) == (
        "New title",
        "New summary",
        "mistral",
    )


def test_init_db_is_safe_to_call_twice():
    save_summary(URL, "Title", "Summary", "llama3.2")

    init_db()  # must not wipe existing data

    assert get_summary(URL) is not None 