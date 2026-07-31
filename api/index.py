"""FastAPI layer over the Python engine, for local dev and Vercel deployment.

This file adds no business logic. Every route below is a thin adapter: it
validates input with `pipeline.validate_inputs`, calls `pipeline.run_report` or
a `report_export` function, and shapes the result into the NDJSON/JSON contract
the frontend expects. The engine modules (reddit_data, discovery, analysis,
pipeline, stats, report_export) are unchanged and stay at the repo root.

Two things have to happen before the engine is imported at all:

1. The repo root has to be on `sys.path`. `uvicorn api.index:app` run from the
   repo root already has the root on `sys.path` (as the current working
   directory), but Vercel's Python runtime invokes this module with its own
   working directory, under which a bare `import pipeline` fails. Inserting
   the parent directory explicitly makes both invocation styles work.
2. On Vercel there is no `gcloud auth application-default login`, so a service
   account key travels as a JSON blob in `GOOGLE_APPLICATION_CREDENTIALS_JSON`.
   That has to become a file with `GOOGLE_APPLICATION_CREDENTIALS` pointing at
   it before analysis.py can build a Vertex client. On this machine neither
   variable is set (real ADC is used instead), so this block is a no-op here.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import queue
import re
import secrets
import sys
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# This writes the service account key to disk once per cold start and never
# deletes it (the function's filesystem disappears with the instance anyway,
# so there is nothing to clean up mid-process). tempfile.NamedTemporaryFile
# already opens the file with mode 0600 on POSIX, so no other process on the
# instance can read it; that default is what actually matters here, not an
# explicit chmod call.
#
# google-genai 1.47.0 (see google/genai/client.py) can accept a
# google.auth.credentials.Credentials object directly instead of a file path,
# which would let this skip the temp file entirely. That change belongs in
# analysis.py's _get_client (the only place genai.Client is constructed), and
# analysis.py is owned by the parallel engine-hardening pass, so it is left
# for that pass rather than done here.
if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON") and not os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS"
):
    _creds_file = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, prefix="gcp-service-account-"
    )
    _creds_file.write(os.environ["GOOGLE_APPLICATION_CREDENTIALS_JSON"])
    _creds_file.close()
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _creds_file.name

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response, StreamingResponse  # noqa: E402

import analysis  # noqa: E402
import db  # noqa: E402
import pipeline  # noqa: E402
import report_export  # noqa: E402

logger = logging.getLogger(__name__)

# uvicorn configures its own "uvicorn.*" loggers and leaves the root logger
# alone, so without this every info line this file and the engine modules emit
# (a run starting, a run being cancelled, how many ideas came back) is dropped,
# and only warnings and errors survive, through logging's last-resort handler.
# That made a cancelled run indistinguishable from silence. basicConfig is a
# no-op when handlers are already attached, so a host that configures logging
# itself, including Vercel's Python runtime, keeps its own setup.
logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s - %(message)s")

app = FastAPI(title="Reddit Content Ideas API")

# Same-origin in production (Next.js and this function share a domain, per the
# spec): VERCEL_ENV is only ever "production" on the production deployment, so
# that is the one place this narrows to a single allowed origin instead of the
# two local dev origins. VERCEL_PROJECT_PRODUCTION_URL is the stable production
# domain Vercel sets on every function invocation; VERCEL_URL is a fallback for
# the rare case that variable is absent.
_IS_PRODUCTION = os.environ.get("VERCEL_ENV") == "production"
if _IS_PRODUCTION:
    _deployment_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
    _CORS_ORIGINS = ["https://{}".format(_deployment_host)] if _deployment_host else []
else:
    _CORS_ORIGINS = ["http://localhost:3000", "http://127.0.0.1:3000"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_PATH = _REPO_ROOT / "sample_report.json"
# Every version this API will still hand to a frontend, mirroring
# SUPPORTED_BUNDLE_VERSIONS in lib/types.ts. A set rather than one number, and
# derived from the engine rather than hardcoded: this was `BUNDLE_VERSION = 4`
# written out by hand, so bumping the engine to 5 silently made the bundled
# sample report unavailable in production while every test still passed.
SUPPORTED_BUNDLE_VERSIONS = frozenset({pipeline.BUNDLE_VERSION, 4})

# analysis.draft_post already caps evidence at DRAFT_MAX_EVIDENCE internally, but
# the API layer does not trust its caller either, so the same cap is applied
# again here before the call is made.
DRAFT_MAX_EVIDENCE = 6
# Cost caps at the boundary: a hand-built request can send arbitrarily long
# strings, and every character here is something a downstream model call pays
# for, so each field is capped before it ever reaches analysis.draft_post.
DRAFT_IDEA_FIELD_MAX_CHARS = 300
DRAFT_EVIDENCE_ITEM_MAX_CHARS = 700
DRAFT_MAX_BODY_BYTES = 64 * 1024

# Saved reports. `secrets.token_urlsafe(12)` is 16 characters of base64url from
# 96 bits of entropy, which is not guessable in any practical sense: a share
# link is the only credential a stored report has, so the id has to be the hard
# part, not the table it lives in.
REPORT_ID_BYTES = 12
# What an id is allowed to look like, applied before any query runs. Deliberately
# a shape check rather than a length check on exactly 16, so ids minted by an
# older or newer token length still resolve.
REPORT_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{8,64}\Z")
REPORT_NOT_FOUND_MESSAGE = "This report link does not exist or was removed."
# The same cap db.upsert_draft applies, stated here so the boundary refuses an
# oversized post with a sentence instead of silently storing a truncated one.
DRAFT_POST_MAX_CHARS = db.MAX_POST_CHARS
# An idea index outside this range cannot belong to any report this engine
# produces, so it is rejected before it can reach the database.
MAX_IDEA_INDEX = 999

# If the worker thread has not posted a progress update in this many seconds
# (a long Vertex or Arctic Shift call), the generator re-sends the last message
# instead of waiting silently, so a proxy in front of the function does not
# treat the connection as idle and close it.
HEARTBEAT_SECONDS = 10

# A soft deadline handed to pipeline.run_report (once it accepts one) so the
# engine itself can start winding down, and a hard stop in the drain loop
# below that always fires before Vercel's 300s function limit, so the stream
# closes cleanly instead of being killed mid-response.
REPORT_SOFT_DEADLINE_SECONDS = 240
REPORT_HARD_STOP_SECONDS = 285
REPORT_TIMEOUT_MESSAGE = (
    "The run took too long and was stopped. Try fewer communities or a shorter time range."
)

# The same sentence app.py shows for an unexpected exception, so the API and
# the rest of the engine never disagree about the generic case.
GENERIC_ERROR_MESSAGE = "Something went wrong while building that report. Please try again."
GENERIC_DRAFT_ERROR_MESSAGE = "Something went wrong while drafting that post. Please try again."

# (builder, media type, file extension, filename suffix). The suffix is what
# keeps two downloads from the same report apart: both CSVs end in ".csv", so
# without it the ideas export and the posts export land in the downloads folder
# under one identical name and the second silently overwrites or "(1)"s the
# first. The Markdown export carries "-report" for the same reason, so all three
# names read the same way: content-ideas-<query slug>-<date>-<kind>.<ext>.
EXPORTERS = {
    "markdown": (report_export.report_markdown, "text/markdown; charset=utf-8", "md", "report"),
    "ideas-csv": (report_export.ideas_csv, "text/csv; charset=utf-8", "csv", "ideas"),
    "posts-csv": (report_export.posts_csv, "text/csv; charset=utf-8", "csv", "posts"),
    "comments-csv": (report_export.comments_csv, "text/csv; charset=utf-8", "csv", "comments"),
}


def _ndjson_line(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _stream_invalid(errors) -> "StreamingResponse":
    async def _single():
        yield _ndjson_line({"type": "invalid", "errors": errors})

    return StreamingResponse(_single(), media_type="application/x-ndjson")


def _accepts_kwarg(func, name: str) -> bool:
    """True if `func` can be called with keyword `name`, today.

    The parallel engine pass is adding `cancel`/`deadline` to run_report and
    `content_types` to validate_inputs, but this file has to work both before
    and after that change lands, so it never assumes the new parameters exist.
    A function that takes **kwargs is treated as accepting anything, which
    covers the fakes tests monkeypatch in as well as the real functions.
    """
    try:
        params = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        param.name == name or param.kind is inspect.Parameter.VAR_KEYWORD for param in params
    )


# ── Origin hardening ────────────────────────────────────────────────────────────
# Defense in depth against cross-site requests hitting the two endpoints that
# actually cost money to run. A same-origin fetch() from the Next.js frontend
# always sends Content-Type: application/json (a value a plain HTML form
# cannot set) and, in every modern browser, a Sec-Fetch-Site header of
# "same-origin"; a cross-site page trying to trigger a run either cannot set
# the header at all or sends a value this rejects.
_ALLOWED_SEC_FETCH_SITE = {"same-origin", "same-site", "none"}


def _reject_cross_origin(request: Request) -> Optional[JSONResponse]:
    content_type = request.headers.get("content-type", "")
    if content_type.split(";")[0].strip().lower() != "application/json":
        return JSONResponse(
            {"message": "Send a JSON request with Content-Type: application/json."},
            status_code=415,
        )

    sec_fetch_site = request.headers.get("sec-fetch-site")
    if sec_fetch_site and sec_fetch_site not in _ALLOWED_SEC_FETCH_SITE:
        return JSONResponse(
            {"message": "This request could not be verified and was rejected."},
            status_code=403,
        )

    return None


# ── Rate limiting ────────────────────────────────────────────────────────────────
# A per-instance, in-memory sliding window. This is defense in depth, not the
# durable fix: it resets whenever the serverless instance recycles and does
# not share state across instances, so the real, durable rate limit belongs
# in a Vercel WAF rate-limiting rule in front of this function. This limiter
# only buys time between "someone finds the endpoint" and "the WAF rule ships".
def _rate_limit_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


REPORTS_PER_HOUR = _rate_limit_env("RATE_LIMIT_REPORTS_PER_HOUR", 6)
DRAFTS_PER_HOUR = _rate_limit_env("RATE_LIMIT_DRAFTS_PER_HOUR", 30)
# Saving an edited draft costs a database round trip and nothing else, so this
# is far more generous than the drafting limit above, which pays for a model
# call every time. It gets its own bucket for exactly that reason: a reader
# tidying up six posts must never use up their remaining generations.
DRAFT_SAVES_PER_HOUR = _rate_limit_env("RATE_LIMIT_DRAFT_SAVES_PER_HOUR", 60)
RATE_LIMIT_WINDOW_SECONDS = 3600

_rate_limit_lock = threading.Lock()
_rate_limit_hits: dict = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


def _rate_limited(bucket: str, ip: str, limit: int) -> bool:
    """True (and records the hit) once `ip` has made `limit` calls to `bucket`
    within the last hour; otherwise records this call and returns False."""
    key = "{}:{}".format(bucket, ip)
    now = time.monotonic()
    with _rate_limit_lock:
        hits = _rate_limit_hits[key]
        while hits and now - hits[0] > RATE_LIMIT_WINDOW_SECONDS:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


def _save_report(query: str, profile: str, bundle) -> Optional[str]:
    """Store a finished run and return the id it can be reached at, or None.

    Persistence is a bonus on top of a run that already succeeded, never a
    condition of it: a database that is down, unconfigured (every local dev
    machine without `.env.local`) or simply slow costs the reader a share link
    and nothing else, so every failure here is one warning line and a None.
    The bundle itself is already on its way to the browser either way.
    """
    if not isinstance(bundle, dict):
        return None
    report_id = secrets.token_urlsafe(REPORT_ID_BYTES)
    try:
        db.save_report(report_id, query, profile, bundle)
    except db.StorageUnavailable as exc:
        logger.warning("report not saved: %s", exc)
        return None
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unexpected failure while saving a report")
        return None
    return report_id


def _run_report_lines(
    query: str, profile: str, days: int, subreddits, relevance_filter: bool, content_types=None,
    phrases=None, channels=None,
):
    """Run pipeline.run_report in a worker thread, yielding NDJSON lines.

    run_report is synchronous and, with a live model and Reddit archive, can
    take minutes, so it cannot run on the event loop. It runs on a plain
    background thread instead. The thread's progress callback pushes onto a
    thread-safe queue.Queue; this generator (itself run in a threadpool by
    Starlette, since it is a plain sync generator) drains that queue and turns
    each entry into one NDJSON line, ending with exactly one terminal line:
    a "report" line with the bundle, or an "error" line with a user-ready
    message.

    A finished run is saved before its terminal line goes out, and the id it
    was saved under rides on that line as `report_id`, next to the bundle
    rather than inside it (see `_save_report`). Saving happens here, on the
    server, rather than in a follow-up call from the browser: the client would
    have to send the whole multi-megabyte bundle back, and a reader who closed
    the tab would lose the report entirely.

    A threading.Event is created here and set again in the `finally` below,
    which runs when this generator is closed early, i.e. when the client
    disconnects; once run_report accepts a `cancel` argument (guarded by
    _accepts_kwarg so this works before and after that lands) that is its
    signal to stop early instead of finishing a run nobody is listening for.
    A `deadline` is passed the same way, so the engine can start winding down
    on its own before the hard stop below fires.
    """
    updates: "queue.Queue[tuple]" = queue.Queue()
    cancel_event = threading.Event()
    deadline = time.monotonic() + REPORT_SOFT_DEADLINE_SECONDS

    def progress(message: str) -> None:
        updates.put(("progress", message))

    def worker() -> None:
        run_kwargs = dict(
            days=days, subreddits=subreddits, relevance_filter=relevance_filter, progress=progress,
        )
        if _accepts_kwarg(pipeline.run_report, "content_types"):
            run_kwargs["content_types"] = list(content_types or [])
        elif _accepts_kwarg(pipeline.run_report, "content_type"):
            # An engine from before the setting took several values: it can still
            # honour one, and one is what the overwhelming majority of runs pick.
            run_kwargs["content_type"] = (content_types or [None])[0]
        if _accepts_kwarg(pipeline.run_report, "channels"):
            # Where the user publishes. An engine that predates channels runs
            # channel-neutral, which is exactly what it did before they existed.
            run_kwargs["channels"] = list(channels or [])
        if _accepts_kwarg(pipeline.run_report, "phrases"):
            # The quoted phrases the validator found in the query. An engine that
            # predates the keyword filter simply never sees them, and the run it
            # does instead is the run it would have done before the feature landed.
            run_kwargs["phrases"] = list(phrases or [])
        if _accepts_kwarg(pipeline.run_report, "cancel"):
            run_kwargs["cancel"] = cancel_event
        if _accepts_kwarg(pipeline.run_report, "deadline"):
            run_kwargs["deadline"] = deadline
        try:
            bundle = pipeline.run_report(query, profile, **run_kwargs)
            updates.put(("report", bundle))
        except pipeline.RunCancelled:
            # Not a failure and not a RunError: the only thing that sets the
            # cancel event is the `finally` below, which runs when the client
            # disconnects. Nothing goes on the queue because there is nobody
            # left to read it, and this is logged at info without a traceback
            # because a user pressing Cancel is the system working.
            logger.info("run cancelled by client")
        except (pipeline.RunError, ValueError) as exc:
            # These already carry a user-ready sentence (see pipeline.py and
            # analysis.py's error mapping), so it is passed straight through.
            updates.put(("error", str(exc)))
        except Exception:  # noqa: BLE001 - last resort, details go to the log only
            logger.exception("unhandled error while building a report")
            updates.put(("error", GENERIC_ERROR_MESSAGE))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    started = time.monotonic()
    last_message = "Starting..."
    try:
        while True:
            # Belt and suspenders alongside the soft deadline above: even if
            # the engine never learns about `deadline`, the stream itself
            # always closes cleanly before Vercel's 300s function limit.
            if time.monotonic() - started > REPORT_HARD_STOP_SECONDS:
                yield _ndjson_line({"type": "error", "message": REPORT_TIMEOUT_MESSAGE})
                return
            try:
                kind, value = updates.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                yield _ndjson_line({"type": "progress", "message": last_message})
                continue

            if kind == "progress":
                last_message = value
                yield _ndjson_line({"type": "progress", "message": value})
            elif kind == "report":
                line = {"type": "report", "bundle": value}
                report_id = _save_report(query, profile, value)
                if report_id:
                    line["report_id"] = report_id
                yield _ndjson_line(line)
                return
            else:
                yield _ndjson_line({"type": "error", "message": value})
                return
    finally:
        cancel_event.set()


@app.post("/api/report")
async def post_report(request: Request):
    origin_error = _reject_cross_origin(request)
    if origin_error is not None:
        return origin_error

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any body that is not valid JSON
        return _stream_invalid(["Send a JSON body with query and profile."])

    if not isinstance(body, dict):
        return _stream_invalid(["Send a JSON body with query and profile."])

    # `content_type` is the single value the form used to send. It is still read
    # here because a shared link or a replayed history entry can carry it, and it
    # means exactly what a one-item `content_types` means.
    validate_kwargs = {}
    if _accepts_kwarg(pipeline.validate_inputs, "content_types"):
        validate_kwargs["content_types"] = body.get("content_types")
    if _accepts_kwarg(pipeline.validate_inputs, "content_type"):
        validate_kwargs["content_type"] = body.get("content_type")
    if _accepts_kwarg(pipeline.validate_inputs, "channels"):
        validate_kwargs["channels"] = body.get("channels")

    checked = pipeline.validate_inputs(
        body.get("query", ""),
        body.get("profile", ""),
        days=body.get("days"),
        subreddits=body.get("subreddits"),
        relevance_filter=body.get("relevance_filter"),
        **validate_kwargs,
    )
    if checked["errors"]:
        return _stream_invalid(checked["errors"])

    if _rate_limited("report", _client_ip(request), REPORTS_PER_HOUR):
        return JSONResponse(
            {"message": "Too many report runs from this network in the last hour. Please try again later."},
            status_code=429,
        )

    chosen_types = checked.get("content_types")
    if chosen_types is None:
        # An engine that still validates one content type at a time.
        legacy = checked.get("content_type")
        chosen_types = [legacy] if legacy else []

    generator = _run_report_lines(
        checked["query"],
        checked["profile"],
        checked["days"],
        checked["subreddits"],
        checked["relevance_filter"],
        content_types=chosen_types,
        # `.get(...) or []` rather than `checked["phrases"]`: an engine from before
        # the keyword filter returns no such key, and version skew must not be a
        # KeyError on the boundary. `channels` is read the same way for the same
        # reason.
        phrases=checked.get("phrases") or [],
        channels=checked.get("channels") or [],
    )
    return StreamingResponse(generator, media_type="application/x-ndjson")


@app.get("/api/reports/{report_id}")
async def get_saved_report(report_id: str):
    """One saved report and every draft written against it.

    Shape: {"bundle": {...}, "drafts": {"<idea_index>": "<post>", ...}}. The id
    in the path is the only credential, so this is deliberately uncacheable:
    `no-store` keeps a shared browser or a proxy from holding on to a report
    somebody else can then read out of the cache, and it also means an edited
    draft is never served stale.

    A malformed id gets the same 404 and the same sentence as an id that simply
    does not exist. There is nothing useful to tell apart, and a distinct
    "that is not a valid id" reply would confirm the shape of real ones.
    """
    headers = {"Cache-Control": "no-store"}
    if not REPORT_ID_PATTERN.match(report_id):
        return JSONResponse(
            {"message": REPORT_NOT_FOUND_MESSAGE}, status_code=404, headers=headers
        )

    try:
        stored = db.get_report(report_id)
    except db.StorageUnavailable as exc:
        return JSONResponse({"message": str(exc)}, status_code=503, headers=headers)
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unexpected failure while reading a saved report")
        return JSONResponse(
            {"message": db.UNAVAILABLE_MESSAGE}, status_code=503, headers=headers
        )

    if stored is None:
        return JSONResponse(
            {"message": REPORT_NOT_FOUND_MESSAGE}, status_code=404, headers=headers
        )

    return JSONResponse(stored, headers=headers)


# {(path, mtime): payload}. The file on disk is the source of truth, so the
# cache key includes the mtime: a regenerated sample_report.json (a new mtime)
# always misses and gets re-parsed, but repeat requests against an unchanged
# file skip the JSON parse entirely. Keyed on the path too, not just the
# mtime, since tests point SAMPLE_PATH at a fresh tmp file per test and two
# unrelated files could otherwise collide on the same mtime.
_sample_cache: dict = {}


def _load_sample_payload() -> dict:
    try:
        mtime = SAMPLE_PATH.stat().st_mtime
    except OSError:
        return {"available": False}

    cache_key = (str(SAMPLE_PATH), mtime)
    if _sample_cache.get("key") == cache_key:
        return _sample_cache["payload"]

    try:
        with open(SAMPLE_PATH, "r", encoding="utf-8") as handle:
            bundle = json.load(handle)
    except (OSError, ValueError):
        bundle = None

    if not isinstance(bundle, dict) \
            or bundle.get("bundle_version") not in SUPPORTED_BUNDLE_VERSIONS:
        payload = {"available": False}
    else:
        payload = {"available": True, "bundle": bundle}

    _sample_cache["key"] = cache_key
    _sample_cache["payload"] = payload
    return payload


@app.get("/api/sample")
async def get_sample():
    payload = _load_sample_payload()
    # Only the hit is cacheable. A miss is usually transient from the caller's
    # side (a sample not deployed yet, a version the file has since caught up
    # with), and an hour-long public cache on it means the frontend's "try again
    # in a moment" is a promise the browser will not honour: the retry is served
    # from cache and fails identically until the entry expires. That is now a
    # user-facing path rather than a dev-only one, so the miss is no-store.
    cache = "public, max-age=3600" if payload.get("available") else "no-store"
    return JSONResponse(payload, headers={"Cache-Control": cache})


@app.get("/api/sample/available")
async def get_sample_available():
    """A cheap existence probe for the mount-time check: just a stat, no JSON
    parse. It intentionally does not confirm the bundle version, so it can
    disagree with GET /api/sample (e.g. a stale v1 file on disk still counts
    as "available" here); callers that need the real bundle still hit the
    full endpoint, this one only decides whether to bother offering it."""
    try:
        available = SAMPLE_PATH.exists() and SAMPLE_PATH.stat().st_size > 0
    except OSError:
        available = False
    return {"available": available}


@app.post("/api/draft")
async def post_draft(request: Request):
    """Draft a publish-ready post for one content idea.

    Plain JSON, no streaming: unlike /api/report this is a single Gemini call
    that returns in normal request time, not a multi-minute pipeline.

    An optional `channel` picks the voice, the hashtag ceiling and the poll cap.
    An unknown one is ignored rather than refused: the post is still writable
    channel-neutral, and a stale client should not lose a draft over it.

    Response shapes (exactly two, both plain JSON so the frontend only ever
    reads either "post" or "error" and never has to branch on status code
    for the message itself):
      - 200 {"post": "..."}: success.
      - 422 {"error": "..."}: the request body itself is malformed (missing
        or non-dict idea, non-list evidence, unparsable JSON). This is a
        client bug, not a service failure, so it gets a 4xx.
      - 500 {"error": "..."}: the body was well-formed but the model layer
        could not produce a post (analysis.draft_post raised ValueError with
        a user-ready sentence, or something else went wrong). This mirrors
        the {"type": "error", "message": ...} terminal line /api/report
        yields for the same kind of failure, just without streaming.
    """
    origin_error = _reject_cross_origin(request)
    if origin_error is not None:
        return origin_error

    raw_body = await request.body()
    if len(raw_body) > DRAFT_MAX_BODY_BYTES:
        return JSONResponse(
            {"message": "That request is too large. Please shorten the idea or evidence and try again."},
            status_code=413,
        )

    try:
        body = json.loads(raw_body)
    except Exception:  # noqa: BLE001 - any body that is not valid JSON
        return JSONResponse(
            {"error": "Send a JSON body with idea, evidence, query and profile."}, status_code=422
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"error": "Send a JSON body with idea, evidence, query and profile."}, status_code=422
        )

    idea = body.get("idea")
    evidence = body.get("evidence")
    if evidence is None:
        evidence = []

    if not isinstance(idea, dict):
        return JSONResponse(
            {"error": "That idea could not be read. Please reopen the report and try again."},
            status_code=422,
        )
    if not isinstance(evidence, list):
        return JSONResponse(
            {"error": "That idea's evidence could not be read. Please reopen the report and try again."},
            status_code=422,
        )

    if _rate_limited("draft", _client_ip(request), DRAFTS_PER_HOUR):
        return JSONResponse(
            {"message": "Too many drafts from this network in the last hour. Please try again later."},
            status_code=429,
        )

    # Coerce every idea field to a plain string and cap it at the boundary:
    # the frontend sends back exactly what the report bundle gave it, but
    # this endpoint does not assume that, since a hand-built or stale request
    # could send anything, and every character here is paid for at the model
    # call this is about to make.
    clean_idea = {
        field: (str(idea.get(field)) if idea.get(field) is not None else "")[:DRAFT_IDEA_FIELD_MAX_CHARS]
        for field in ("title", "hook", "format", "angle", "addresses")
    }
    clean_evidence = [
        str(item)[:DRAFT_EVIDENCE_ITEM_MAX_CHARS] for item in evidence[:DRAFT_MAX_EVIDENCE]
    ]
    query = str(body.get("query") or "")[: pipeline.MAX_QUERY_CHARS]
    profile = str(body.get("profile") or "")[: pipeline.MAX_PROFILE_CHARS]
    channel = _draft_channel(body)

    draft_kwargs = {}
    if channel and _accepts_kwarg(analysis.draft_post, "channel"):
        draft_kwargs["channel"] = channel

    try:
        result = analysis.draft_post(
            clean_idea, clean_evidence, query, profile, **draft_kwargs
        )
    except ValueError as exc:
        # A user-ready sentence from the model layer, same treatment as the
        # RunError/ValueError branch in _run_report_lines.
        return JSONResponse({"error": str(exc)}, status_code=500)
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unhandled error while drafting a post")
        return JSONResponse({"error": GENERIC_DRAFT_ERROR_MESSAGE}, status_code=500)

    # A post that was just paid for should survive a refresh without the reader
    # having to touch it first, so a request that says which saved report and
    # which idea it belongs to gets the result stored on the way out. Purely
    # best effort: the post is already written, and no storage failure is worth
    # turning a successful draft into an error the reader has to retry.
    saved_to = _draft_target(body)
    if saved_to is not None:
        _persist_draft(saved_to[0], saved_to[1], result["post"])

    return {"post": result["post"]}


def _draft_channel(body: dict) -> str:
    """The channel a draft request named, or "" for channel-neutral.

    Dropped rather than refused when it is not one we support, matching how the
    engine treats an unknown channel everywhere else: the draft is still writable
    without it, and a stale link should not cost the reader a post.
    """
    value = body.get("channel")
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    # getattr, not attribute access: this file is deployed against engines either
    # side of the change, and an older analysis module has no CHANNELS at all.
    return text if text in getattr(analysis, "CHANNELS", {}) else ""


def _draft_target(body: dict):
    """(report_id, idea_index) when a draft request names a saved report and a
    valid idea inside it, otherwise None. Both fields are optional: a run whose
    bundle never reached the database sends neither."""
    report_id = body.get("report_id")
    idea_index = body.get("idea_index")
    if not isinstance(report_id, str) or not REPORT_ID_PATTERN.match(report_id):
        return None
    # bool is an int subclass, and `True` is not an idea index.
    if isinstance(idea_index, bool) or not isinstance(idea_index, int):
        return None
    if idea_index < 0 or idea_index > MAX_IDEA_INDEX:
        return None
    return report_id, idea_index


def _persist_draft(report_id: str, idea_index: int, post: str) -> bool:
    """Store one draft, swallowing every failure into a warning. True when it
    landed."""
    try:
        db.upsert_draft(report_id, idea_index, post)
    except db.StorageUnavailable as exc:
        logger.warning("draft not saved: %s", exc)
        return False
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unexpected failure while saving a draft")
        return False
    return True


@app.post("/api/drafts")
async def post_save_draft(request: Request):
    """Save an edited draft against a saved report.

    Unlike /api/draft this writes nothing but a row: the post arrives already
    written, usually because the reader edited the generated one. Response is
    {"saved": true} or a 4xx/503 with a message, and it is the one draft route
    that is allowed to fail loudly, because a silent failure here would mean a
    reader believing their edits are safe when they are not.
    """
    origin_error = _reject_cross_origin(request)
    if origin_error is not None:
        return origin_error

    raw_body = await request.body()
    if len(raw_body) > DRAFT_MAX_BODY_BYTES:
        return JSONResponse(
            {"message": "That post is too long to save. Please shorten it and try again."},
            status_code=413,
        )

    try:
        body = json.loads(raw_body)
    except Exception:  # noqa: BLE001 - any body that is not valid JSON
        body = None
    if not isinstance(body, dict):
        return JSONResponse(
            {"message": "Send a JSON body with report_id, idea_index and post."},
            status_code=422,
        )

    target = _draft_target(body)
    if target is None:
        return JSONResponse(
            {"message": "That draft could not be saved because the report it belongs to was not recognised."},
            status_code=422,
        )

    post = body.get("post")
    if not isinstance(post, str) or not post.strip():
        return JSONResponse(
            {"message": "There is nothing in that draft to save."}, status_code=422
        )
    if len(post) > DRAFT_POST_MAX_CHARS:
        return JSONResponse(
            {"message": "That post is too long to save. Please shorten it and try again."},
            status_code=413,
        )

    if _rate_limited("draft-save", _client_ip(request), DRAFT_SAVES_PER_HOUR):
        return JSONResponse(
            {"message": "Too many draft saves from this network in the last hour. Please try again later."},
            status_code=429,
        )

    if not _persist_draft(target[0], target[1], post):
        return JSONResponse({"message": db.UNAVAILABLE_MESSAGE}, status_code=503)

    return {"saved": True}


def _bundle_from_payload(payload) -> Optional[dict]:
    if not isinstance(payload, dict):
        return None
    bundle = payload.get("bundle")
    if not isinstance(bundle, dict) or "bundle_version" not in bundle:
        return None
    return bundle


@app.post("/api/export/{kind}")
async def post_export(kind: str, request: Request):
    exporter = EXPORTERS.get(kind)
    if exporter is None:
        return JSONResponse({"message": "Unknown export type."}, status_code=404)

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - any body that is not valid JSON
        return JSONResponse({"message": "Send a JSON body with the report bundle to export."}, status_code=400)

    bundle = _bundle_from_payload(payload)
    if bundle is None:
        return JSONResponse({"message": "Send a JSON body with the report bundle to export."}, status_code=400)

    builder, media_type, extension, suffix = exporter
    try:
        content = builder(bundle)
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("export builder failed for kind=%s", kind)
        return JSONResponse({"message": "That export could not be built."}, status_code=400)

    filename = "{}.{}".format(report_export.filename_stem(bundle, suffix), extension)
    headers = {"Content-Disposition": 'attachment; filename="{}"'.format(filename)}
    return Response(content=content, media_type=media_type, headers=headers)
