import os

import requests

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2"
TIMEOUT_SECONDS = 120      # first request is slow: the model loads into memory
MAX_INPUT_CHARS = 12_000   # ~3k tokens; longer articles are truncated
CONTEXT_TOKENS = 8192      # must fit prompt + article + answer
MAX_OUTPUT_TOKENS = 600

SYSTEM_PROMPT = (
    "You are an assistant for financial analysts. Summarize the article in 3-5 "
    "concise bullet points. Focus on key facts, figures, companies involved and "
    "market implications. Use only information from the article."
)


class SummarizerError(Exception):
    """Raised when the local language model call fails."""


def get_model_name() -> str:
    return os.getenv("OLLAMA_MODEL", DEFAULT_MODEL)

def summarize(title: str, text: str) -> str:
    base_url = os.getenv("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")
    model = get_model_name()

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Title: {title}\n\n{text[:MAX_INPUT_CHARS]}"},
        ],
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_ctx": CONTEXT_TOKENS,
            "num_predict": MAX_OUTPUT_TOKENS,
        },
    }

    try:
        resp = requests.post(
            f"{base_url}/api/chat", json=payload, timeout=TIMEOUT_SECONDS
        )
    except requests.Timeout as exc:
        raise SummarizerError("The model took too long to respond") from exc
    except requests.ConnectionError as exc:
        raise SummarizerError(
            f"Cannot connect to Ollama at {base_url}. Is it running?"
        ) from exc
    except requests.RequestException as exc:
        raise SummarizerError(f"Model request failed: {exc}") from exc

    if resp.status_code != 200:
        raise SummarizerError(
            f"Ollama returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    try:
        content = resp.json()["message"]["content"]
    except (ValueError, KeyError, TypeError) as exc:
        raise SummarizerError("Unexpected response format from Ollama") from exc

    if not content or not content.strip():
        raise SummarizerError("The model returned an empty summary")

    return content.strip()