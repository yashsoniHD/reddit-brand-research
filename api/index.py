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

import json
import logging
import os
import queue
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

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
import pipeline  # noqa: E402
import report_export  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Reddit Content Ideas API")

# Same-origin in production (Next.js and this function share a domain, per the
# spec), but harmless to also allow the Next.js dev server so local development
# does not need its own CORS workaround.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_PATH = _REPO_ROOT / "sample_report.json"
BUNDLE_VERSION = 4

# analysis.draft_post already caps evidence at DRAFT_MAX_EVIDENCE internally, but
# the API layer does not trust its caller either, so the same cap is applied
# again here before the call is made.
DRAFT_MAX_EVIDENCE = 6

# If the worker thread has not posted a progress update in this many seconds
# (a long Vertex or Arctic Shift call), the generator re-sends the last message
# instead of waiting silently, so a proxy in front of the function does not
# treat the connection as idle and close it.
HEARTBEAT_SECONDS = 10

# The same sentence app.py shows for an unexpected exception, so the API and
# the (soon to be removed) Streamlit UI never disagree about the generic case.
GENERIC_ERROR_MESSAGE = "Something went wrong while building that report. Please try again."
GENERIC_DRAFT_ERROR_MESSAGE = "Something went wrong while drafting that post. Please try again."

EXPORTERS = {
    "markdown": (report_export.report_markdown, "text/markdown; charset=utf-8", "md"),
    "ideas-csv": (report_export.ideas_csv, "text/csv; charset=utf-8", "csv"),
    "posts-csv": (report_export.posts_csv, "text/csv; charset=utf-8", "csv"),
}


def _ndjson_line(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def _stream_invalid(errors) -> "StreamingResponse":
    async def _single():
        yield _ndjson_line({"type": "invalid", "errors": errors})

    return StreamingResponse(_single(), media_type="application/x-ndjson")


def _run_report_lines(query: str, profile: str, days: int, subreddits, relevance_filter: bool):
    """Run pipeline.run_report in a worker thread, yielding NDJSON lines.

    run_report is synchronous and, with a live model and Reddit archive, can
    take minutes, so it cannot run on the event loop. It runs on a plain
    background thread instead. The thread's progress callback pushes onto a
    thread-safe queue.Queue; this generator (itself run in a threadpool by
    Starlette, since it is a plain sync generator) drains that queue and turns
    each entry into one NDJSON line, ending with exactly one terminal line:
    a "report" line with the bundle, or an "error" line with a user-ready
    message.
    """
    updates: "queue.Queue[tuple]" = queue.Queue()

    def progress(message: str) -> None:
        updates.put(("progress", message))

    def worker() -> None:
        try:
            bundle = pipeline.run_report(
                query,
                profile,
                days=days,
                subreddits=subreddits,
                relevance_filter=relevance_filter,
                progress=progress,
            )
            updates.put(("report", bundle))
        except (pipeline.RunError, ValueError) as exc:
            # These already carry a user-ready sentence (see pipeline.py and
            # analysis.py's error mapping), so it is passed straight through.
            updates.put(("error", str(exc)))
        except Exception:  # noqa: BLE001 - last resort, details go to the log only
            logger.exception("unhandled error while building a report")
            updates.put(("error", GENERIC_ERROR_MESSAGE))

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    last_message = "Starting..."
    while True:
        try:
            kind, value = updates.get(timeout=HEARTBEAT_SECONDS)
        except queue.Empty:
            yield _ndjson_line({"type": "progress", "message": last_message})
            continue

        if kind == "progress":
            last_message = value
            yield _ndjson_line({"type": "progress", "message": value})
        elif kind == "report":
            yield _ndjson_line({"type": "report", "bundle": value})
            return
        else:
            yield _ndjson_line({"type": "error", "message": value})
            return


@app.post("/api/report")
async def post_report(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any body that is not valid JSON
        return _stream_invalid(["Send a JSON body with query and profile."])

    if not isinstance(body, dict):
        return _stream_invalid(["Send a JSON body with query and profile."])

    checked = pipeline.validate_inputs(
        body.get("query", ""),
        body.get("profile", ""),
        days=body.get("days"),
        subreddits=body.get("subreddits"),
        relevance_filter=body.get("relevance_filter"),
    )
    if checked["errors"]:
        return _stream_invalid(checked["errors"])

    generator = _run_report_lines(
        checked["query"],
        checked["profile"],
        checked["days"],
        checked["subreddits"],
        checked["relevance_filter"],
    )
    return StreamingResponse(generator, media_type="application/x-ndjson")


@app.get("/api/sample")
async def get_sample():
    # Read fresh every call, deliberately uncached: the file on disk is the
    # single source of truth and may be regenerated between requests.
    try:
        with open(SAMPLE_PATH, "r", encoding="utf-8") as handle:
            bundle = json.load(handle)
    except (OSError, ValueError):
        return {"available": False}

    if not isinstance(bundle, dict) or bundle.get("bundle_version") != BUNDLE_VERSION:
        return {"available": False}

    return {"available": True, "bundle": bundle}


@app.post("/api/draft")
async def post_draft(request: Request):
    """Draft a publish-ready LinkedIn post for one content idea.

    Plain JSON, no streaming: unlike /api/report this is a single Gemini call
    that returns in normal request time, not a multi-minute pipeline.

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
    try:
        body = await request.json()
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

    # Coerce every idea field to a plain string: the frontend sends back
    # exactly what the report bundle gave it, but this endpoint does not
    # assume that, since a hand-built or stale request could send anything.
    clean_idea = {
        field: str(idea.get(field)) if idea.get(field) is not None else ""
        for field in ("title", "hook", "format", "angle", "addresses")
    }
    clean_evidence = [str(item) for item in evidence[:DRAFT_MAX_EVIDENCE]]
    query = str(body.get("query") or "")
    profile = str(body.get("profile") or "")

    try:
        result = analysis.draft_post(clean_idea, clean_evidence, query, profile)
    except ValueError as exc:
        # A user-ready sentence from the model layer, same treatment as the
        # RunError/ValueError branch in _run_report_lines.
        return JSONResponse({"error": str(exc)}, status_code=500)
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unhandled error while drafting a post")
        return JSONResponse({"error": GENERIC_DRAFT_ERROR_MESSAGE}, status_code=500)

    return {"post": result["post"]}


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

    builder, media_type, extension = exporter
    content = builder(bundle)
    filename = "{}.{}".format(report_export.filename_stem(bundle), extension)
    headers = {"Content-Disposition": 'attachment; filename="{}"'.format(filename)}
    return Response(content=content, media_type=media_type, headers=headers)
