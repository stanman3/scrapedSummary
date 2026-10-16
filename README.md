# Financial Article Summarizer

A small web service for NovaBridge Finance analysts: paste the URL of a public financial news article and get a short bullet-point summary. The backend downloads and parses the page, summarizes it with a **local** language model (Ollama), and caches the result so the same article is never processed twice.

## How it works

```
Browser ──POST /api/summarize {url}──▶ FastAPI (main.py)
                                          │
                       1. validate URL ───┤ 400 if not a proper http(s) URL
                       2. cache lookup ───┤ db.py  (SQLite) ──▶ hit: return immediately
                       3. fetch + parse ──┤ scraper.py (requests + BeautifulSoup)
                       4. summarize ──────┤ summarizer.py (Ollama /api/chat)
                       5. cache + return ─┘
```

## Project structure

```
summarizer/
├── main.py            # FastAPI app: routes, validation, error mapping
├── scraper.py         # download HTML, extract title + article text
├── summarizer.py      # call the local LLM, return the summary text
├── db.py              # SQLite cache + URL normalization
├── static/
│   └── index.html     # single-file frontend (plain HTML/CSS/JS, talks to /api/summarize)
├── tests/
│   ├── test_scraper.py
│   ├── test_summarizer.py
│   └── test_db.py
├── ai-logs/           # exported AI assistant conversations
├── requirements.txt
├── pytest.ini
└── README.md
```

`scraper.py` and `summarizer.py` each expose one small error type (`ScraperError`, `SummarizerError`), and `db.py` lets `sqlite3.Error` propagate, so `main.py` only decides *what HTTP response* each failure becomes.

## Setup

Requires **Python 3.10+** and **[Ollama](https://ollama.com)**.

```bash
git clone <repo-url> && cd summarizer

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Local model (one-time download)
ollama pull llama3.2
```

## Run

Make sure Ollama is running (`ollama serve`, or the desktop app), then:

```bash
uvicorn main:app --reload
```

Open <http://localhost:8000>, paste an article URL and press submit. The first request after starting Ollama is slow (up to about a minute) because the model is loaded into memory. Interactive API docs are available at <http://localhost:8000/docs>.

To use another model, set the variable when starting the server, for example:

```bash
OLLAMA_MODEL=qwen2.5:3b uvicorn main:app --reload
```

### Configuration (environment variables)

| Variable       | Default                  | Purpose                              |
|----------------|--------------------------|--------------------------------------|
| `OLLAMA_URL`   | `http://localhost:11434` | Where the Ollama server is running   |
| `OLLAMA_MODEL` | `llama3.2`               | Model used for summarization (name as shown by `ollama list`) |
| `DB_PATH`      | `summaries.db`           | SQLite file used as the cache        |

## API

### `POST /api/summarize`

Request:

```json
{ "url": "https://example.com/markets/rates-held" }
```

Response `200`:

```json
{
  "url": "https://example.com/markets/rates-held",
  "title": "Central bank holds rates",
  "summary": "- The central bank kept rates unchanged...\n- ...",
  "model": "llama3.2",
  "cached": false
}
```

`url` is the *normalized* form of the submitted URL (see below). `cached` tells the client whether the summary came from the cache, and `model` is the model that produced it.

Errors (body: `{"detail": "..."}`):

| Status | When                                                                                  |
|--------|---------------------------------------------------------------------------------------|
| `400`  | Not a valid http(s) URL (wrong scheme, no host, bad port, malformed address)          |
| `422`  | Request body invalid (missing field, URL over 2048 chars) **or** the page was fetched but no article text could be found (paywalled / JavaScript-rendered / not an article) |
| `502`  | The page could not be downloaded, or the language model call failed                   |

Note: for request-body validation errors FastAPI/pydantic returns `detail` as a *list* of objects instead of a string; in that case the frontend shows a generic "request was rejected" message.

### `GET /`

Serves the frontend (`static/index.html`): a URL field, a submit button, a loading state, the summary as a bullet list, and a line showing whether it came from the cache and which model was used. Errors from the API are displayed as text.

## Tests

```bash
pip install -r requirements-dev.txt   # [TODO: file with pytest and the test dependencies, or merge into requirements.txt]
pytest
```

No network access and no running Ollama are needed: HTTP calls are mocked and every database test uses its own temporary SQLite file.

| File                      | What it covers                                                                                              |
|---------------------------|-------------------------------------------------------------------------------------------------------------|
| `tests/test_scraper.py`   | Title/body extraction, noise removal, fallback when there is no `<article>`, too-little-text error, non-http URLs rejected before any request, network/HTTP error wrapping, encoding fallback |
| `tests/test_summarizer.py`| Request payload (model, URL from env), stripped output, input truncation, every failure mode (timeout, connection, non-200, bad JSON, missing key, empty answer) |
| `tests/test_db.py`        | URL normalization (equivalent and non-equivalent URLs, idempotence), cache hit/miss, overwrite, repeated `init_db()` |
| [TODO: `tests/test_main.py` if it exists, otherwise delete this row] | API behavior: status codes for bad input and upstream failures, cache hit/miss, cache failures not breaking a request |

The frontend has no automated tests.

## Design decisions and trade-offs

**Small modules with narrow interfaces.** Fetching, parsing, summarizing and storage are independent functions. That is what makes each one testable in isolation with mocks, and it keeps `main.py` to orchestration and HTTP concerns. I kept the structure deliberately flat (four modules, no service layer or provider interface) so the whole system is easy to read and explain.

**Plain `def` route instead of `async def`.** Downloading a page and calling the model are blocking calls. A plain `def` endpoint is run by FastAPI in a thread pool, so one slow request does not freeze the event loop. Going fully async (`httpx`) would scale better but add complexity that a prototype does not need.

**Local model via Ollama.** Article text never leaves the machine (relevant for a financial-advisory firm), no API key or account is needed and there are no rate limits. The cost is setup for the reviewer (install Ollama, pull a model), slower responses and lower summary quality than a hosted frontier model. The model name and server URL are configurable, and the call is isolated in `summarizer.py`, so switching providers means changing one function. LangChain is not used: this is a single HTTP call and a thin wrapper is easier to read, test and explain. The native `/api/chat` endpoint is used instead of the OpenAI-compatible one because it allows setting the context window (`num_ctx`); with a small default context Ollama silently drops the start of a long input.

**SQLite cache, one connection per call.** SQLite needs no server and is enough for this workload. `sqlite3` connections cannot be shared across threads and FastAPI runs the endpoint in a thread pool, so each operation opens and closes its own connection. `INSERT OR REPLACE` means two requests saving the same URL cannot fail on the unique key (last write wins); a locked database is handled by the non-fatal cache handling described below.

**Model name stored with each summary.** The response and the database record which model produced a summary. The cache key is the URL only, so changing the model does not invalidate existing entries; keying by `(url, model)` would.

**URL normalization for the cache key.** `https://Example.com/a/?utm_source=x#top` and `https://example.com/a` are the same article and share one cache entry: scheme and host are lowercased, fragments and tracking parameters (`utm_*`, `fbclid`, `gclid`) are dropped, remaining query parameters (including blank ones) are sorted and re-encoded, trailing slashes are removed. Deliberately **not** merged because they can be different pages: `http` vs `https`, `www.` vs bare host, different path case. The page is always fetched using the URL as submitted; only the cache key is normalized.

**The cache is an optimization, not a dependency.** If reading or writing the cache fails, the error is logged and the request still succeeds. A full disk should not throw away a summary the model already produced.

**Heuristic article extraction.** The scraper uses `requests` + BeautifulSoup (`html.parser`): remove obvious noise tags (`script`, `style`, `nav`, `footer`, `aside`), prefer paragraphs inside `<article>`, fall back to all paragraphs on the page, drop very short paragraphs (menus, captions) and fail if too little text remains. This works for most news sites without per-site rules. `<form>` is intentionally *not* treated as noise: some older sites wrap the entire page in one form. A dedicated library (e.g. trafilatura) would extract more accurately; I kept to the suggested stack and a readable, testable heuristic.

**Explicit, differentiated errors.** Every expected failure becomes a specific HTTP status and a human-readable message instead of a generic 500: bad input (400), page without extractable article (422), upstream download or model failure (502). Timeouts, connection errors and HTTP errors are caught where they happen and re-raised as the module's own error type.

**Bounded model input.** Articles are cut to 12,000 characters (about 3k tokens) so prompt, article and answer fit the 8k context window configured for the model.

**Single-file frontend with no build step.** Plain HTML, CSS and `fetch`, as the brief says functionality matters more than polish. Everything that comes from the server (article title, model output, error messages) is inserted with `textContent`, never as HTML, so a hostile page title or model answer cannot inject markup.

## Known limitations and what I would do next

- **Truncation is silent.** Very long articles are shortened before summarization and the user is not told. Next step: return a `truncated` flag from the summarizer, store it in the cache and show a notice in the UI.
- **SSRF.** The service downloads any URL a user submits, including `localhost` and private network addresses. Fix: resolve the host and reject private, loopback and link-local ranges (also after redirects). Until then the service should not be exposed to untrusted users.
- **No response-size or content-type limit.** A link to a huge file or a PDF would be downloaded in full. Fix: check `Content-Type` and stream with a byte cap.
- **Prompt injection.** Article text is untrusted and is placed in the prompt; a page can contain instructions aimed at the model. Mitigation: delimit the article and instruct the model to treat it as data only; output is displayed as text, never executed.
- **No cache expiry.** Cached summaries live forever. `created_at` is stored, so a TTL would be easy to add.
- **No schema migrations.** The table is created if missing; after a schema change the cache file (`summaries.db`) has to be deleted.
- **Duplicate work for simultaneous requests.** Two concurrent requests for the same uncached URL both run the model. A per-URL lock or in-flight map would fix it.
- **Blocking concurrency.** Each request occupies a worker thread for up to the 120 s model timeout. A job queue with polling (or streaming) would suit real traffic.
- **Coarse status codes and error text.** All upstream failures are 502 (a model timeout is arguably 504, a missing model 503), and error messages can include the Ollama address. Acceptable for an internal prototype; in production, log details and return generic messages.
- **Plain-text output.** The model's answer is shown as text; Markdown formatting (e.g. `**bold**`) is not rendered.
- **JavaScript-rendered and paywalled pages** cannot be summarized (no headless browser); they return a clear 422.
- **No authentication or rate limiting.**
- **No Dockerfile.** Running in a container would need `OLLAMA_URL` to point at the host (e.g. `http://host.docker.internal:11434`) or an Ollama service in a compose file.

## AI assistant usage

I used **Claude** (Anthropic) in **two separate conversations**. One conversation I used only for **generating the first versions of `scraper.py`, `summarizer.py`, `db.py`, `main.py` and `static/index.html`** The other conversation I used for descussing, validation, test generation and overall assessment of the project - complete export is in `ai-logs.txt`.

My own part: I asked for a deliberately small design instead of a larger layered one, chose Ollama as the model backend and set it up locally, ran the application against real articles, ran the test suite, and decided which review findings to apply. The review changed the code in several places: removing `form` from the scraper's noise tags, keeping blank query values in URL normalization, handling malformed URLs in validation, and making cache failures non-fatal.

Most of the application code was therefore generated by Claude rather than written by hand. I have gone through every file and can walk through how it works and why it is designed this way.