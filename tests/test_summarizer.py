from unittest.mock import Mock, patch

import pytest
import requests

from summarizer import (
    DEFAULT_MODEL,
    MAX_INPUT_CHARS,
    SummarizerError,
    get_model_name,
    summarize,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Make sure the developer's own OLLAMA_* settings don't leak into tests."""
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)


def _ok_response(content="- Rates held steady.\n- Yields rose."):
    resp = Mock(status_code=200, text="")
    resp.json.return_value = {"message": {"role": "assistant", "content": content}}
    return resp


def _user_message(mock_post) -> str:
    """The article text that was actually sent to the model."""
    return mock_post.call_args.kwargs["json"]["messages"][1]["content"]


# ---------- configuration ----------


def test_model_name_defaults_when_env_is_not_set():
    assert get_model_name() == DEFAULT_MODEL


def test_model_name_comes_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")

    assert get_model_name() == "mistral"


# ---------- happy path ----------


def test_returns_stripped_summary():
    with patch("summarizer.requests.post", return_value=_ok_response("  - Point one  \n")):
        assert summarize("Title", "Some article text.") == "- Point one"


def test_env_vars_control_url_and_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama.internal:9999/")  # trailing slash
    monkeypatch.setenv("OLLAMA_MODEL", "mistral")

    with patch("summarizer.requests.post", return_value=_ok_response()) as mock_post:
        summarize("Title", "Some article text.")

    assert mock_post.call_args.args[0] == "http://ollama.internal:9999/api/chat"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "mistral"
    assert payload["stream"] is False


def test_title_and_text_are_sent_to_the_model():
    with patch("summarizer.requests.post", return_value=_ok_response()) as mock_post:
        summarize("Rates held", "The central bank acted.")

    sent = _user_message(mock_post)
    assert "Rates held" in sent
    assert "The central bank acted." in sent


# ---------- truncation ----------


def test_long_article_is_cut_to_the_input_limit():
    long_text = "x" * (MAX_INPUT_CHARS + 5_000)

    with patch("summarizer.requests.post", return_value=_ok_response()) as mock_post:
        summarize("Title", long_text)

    sent = _user_message(mock_post)
    assert "x" * MAX_INPUT_CHARS in sent
    assert "x" * (MAX_INPUT_CHARS + 1) not in sent


def test_article_at_the_limit_is_sent_in_full():
    with patch("summarizer.requests.post", return_value=_ok_response()) as mock_post:
        summarize("Title", "x" * MAX_INPUT_CHARS)

    assert "x" * MAX_INPUT_CHARS in _user_message(mock_post)


# ---------- failures ----------


@pytest.mark.parametrize(
    "error, message",
    [
        (requests.Timeout("slow"), "too long"),
        (requests.ConnectionError("refused"), "Cannot connect"),
        (requests.RequestException("boom"), "request failed"),
    ],
)
def test_network_errors_become_summarizer_error(error, message):
    with patch("summarizer.requests.post", side_effect=error):
        with pytest.raises(SummarizerError, match=message):
            summarize("Title", "text")


def test_non_200_status_raises_with_status_in_message():
    resp = Mock(status_code=404, text='{"error":"model not found"}')
    with patch("summarizer.requests.post", return_value=resp):
        with pytest.raises(SummarizerError, match="404"):
            summarize("Title", "text")


def test_invalid_json_raises():
    resp = Mock(status_code=200, text="<html>oops</html>")
    resp.json.side_effect = ValueError("not json")
    with patch("summarizer.requests.post", return_value=resp):
        with pytest.raises(SummarizerError, match="Unexpected response"):
            summarize("Title", "text")


def test_missing_message_key_raises():
    resp = Mock(status_code=200, text="")
    resp.json.return_value = {"done": True}
    with patch("summarizer.requests.post", return_value=resp):
        with pytest.raises(SummarizerError, match="Unexpected response"):
            summarize("Title", "text")


@pytest.mark.parametrize("content", ["", "   \n  ", None])
def test_empty_summary_raises(content):
    with patch("summarizer.requests.post", return_value=_ok_response(content)):
        with pytest.raises(SummarizerError, match="empty"):
            summarize("Title", "text")