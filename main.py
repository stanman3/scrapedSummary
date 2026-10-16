import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from db import get_summary, init_db, normalize_url, save_summary
from scraper import ScraperError, extract_article, fetch_html
from summarizer import SummarizerError, get_model_name, summarize

logger = logging.getLogger(__name__)

INDEX_FILE = Path(__file__).parent / "static" / "index.html"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Financial Article Summarizer", lifespan=lifespan)


class SummarizeRequest(BaseModel):
    url: str = Field(max_length=2048)


class SummaryResponse(BaseModel):
    url: str
    title: str
    summary: str
    model: str
    cached: bool


def _is_valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        _ = parsed.port  # raises ValueError for an invalid port
    except ValueError:  # e.g. "http://[::1" (invalid IPv6)
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(INDEX_FILE)


# Plain `def` (not async): fetching and the LLM call are blocking, so FastAPI
# runs this in a thread pool instead of freezing the event loop.
@app.post("/api/summarize", response_model=SummaryResponse)
def summarize_url(req: SummarizeRequest):
    url = req.url.strip()
    if not _is_valid_http_url(url):
        raise HTTPException(status_code=400, detail="Please provide a valid http(s) URL")

    # The cache is an optimization: a broken database must not break the request.
    try:
        cached = get_summary(url)
    except sqlite3.Error:
        logger.exception("Could not read cache for %s", url)
        cached = None
    if cached:
        return SummaryResponse(
            url=cached["url"],
            title=cached["title"],
            summary=cached["summary"],
            model=cached["model"],
            cached=True,
        )

    try:
        html = fetch_html(url)
    except ScraperError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        title, text = extract_article(html)
    except ScraperError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        summary = summarize(title, text)
    except SummarizerError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    model = get_model_name()
    try:
        save_summary(url, title, summary, model)
    except sqlite3.Error:
        logger.exception("Could not cache summary for %s", url)
    return SummaryResponse(
        url=normalize_url(url), title=title, summary=summary, model=model, cached=False
    )