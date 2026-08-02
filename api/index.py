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
import re
import secrets
import sys
import tempfile
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import NamedTuple, Optional

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

import jwt  # noqa: E402
from fastapi import Depends, FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

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
# A run is private to whoever started it (unlike a shareable report), so a run
# that belongs to someone else is answered exactly like one that does not exist:
# one 404 and one sentence, with nothing to tell the two apart.
RUN_NOT_FOUND_MESSAGE = "This run does not exist or is no longer available."
# The one sentence the worker route returns for any authentication failure:
# a missing bearer, a wrong secret, or a deployment with no CRON_SECRET at all.
# Collapsed like the 401 sentence everywhere else so a caller probing the route
# learns nothing about which of the three tripped.
CRON_AUTH_FAILED_MESSAGE = "This endpoint is not available."
# The same cap db.upsert_draft applies, stated here so the boundary refuses an
# oversized post with a sentence instead of silently storing a truncated one.
DRAFT_POST_MAX_CHARS = db.MAX_POST_CHARS
# An idea index outside this range cannot belong to any report this engine
# produces, so it is rejected before it can reach the database.
MAX_IDEA_INDEX = 999

# A soft deadline handed to pipeline.run_report so the engine can start winding
# down on its own well before Vercel's 300s function limit ends the worker
# invocation this run executes inside. There is no hard stop in a drain loop any
# more: a run is one worker invocation, not a stream held open to a browser, so
# the platform's own maxDuration is the outer bound and this is the inner one the
# engine honours cleanly.
REPORT_SOFT_DEADLINE_SECONDS = 240

# How the worker keeps the run row honest while pipeline.run_report is blocking.
# Progress strings accumulate in memory and are flushed to the row at most this
# often (and always once more on the terminal write), which also refreshes
# heartbeat_at so claim_next_run's stale-heartbeat reaper does not re-queue a
# run that is simply mid-stage on a long model call.
PROGRESS_FLUSH_SECONDS = 2.0
# How often the cancellation poller asks the database whether the owner has
# pressed Cancel. The engine only stops at stage boundaries, so polling faster
# than a stage takes buys nothing but round trips.
CANCEL_POLL_SECONDS = 3.0

# A completed run's terminal write must survive a momentary database blip. The
# pipeline has already run by the time set_run_result is called, so if that one
# write is lost the row stays `running`, its heartbeat goes stale, and the reaper
# re-executes a run that actually finished, paying for the whole pipeline again.
# So the terminal write is retried a few times before it is given up on, and
# giving up logs loudly rather than raising: the worker route then still returns a
# plain 200 (the run did complete), never a 500 that reads to pg_net as "retry
# me". Progress writes stay best-effort and swallowed as before; only the one
# write that records the outcome is worth retrying.
TERMINAL_WRITE_ATTEMPTS = 3
TERMINAL_WRITE_RETRY_SECONDS = 0.5

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


def _invalid_response(errors) -> "JSONResponse":
    """The validation-failure body, as plain JSON rather than an NDJSON line.

    Same shape the old streaming terminal line carried, {"type": "invalid",
    "errors": [...]}, so the frontend reads `type` and `errors` exactly as it did
    when this was the first (and only) line of a stream. A 200 for the same reason
    the stream was a 200: a rejected brief is a normal answer to a well-formed
    request, not a transport error, and the frontend decides what to show off the
    body, not the status.
    """
    return JSONResponse({"type": "invalid", "errors": errors})


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


# ── Authentication ──────────────────────────────────────────────────────────────
# Every route in this file requires a Supabase access token, and this file is the
# only thing that can require it. In production vercel.json rewrites /api/:path*
# straight to this function, so Next.js and proxy.ts never see an API request at
# all: a check that lived only in the proxy would be no check anywhere.
#
# Only asymmetric signing is accepted. This project sits on Supabase's signing
# keys, which publish the public half of an ES256 keypair as a JWKS document, so
# every token is verified against a fetched public key and nothing else. The
# legacy HS256 path (the shared SUPABASE_JWT_SECRET) is deliberately gone: a
# secret that verifies a token can also mint one, and keeping it would leave a
# second forging key in the deployment for no benefit here. The token's header
# still only ever names one algorithm, it must be in the accepted set below, and
# `decode` is pinned to that single value, so rewriting `alg` (the classic
# confusion attack, or `alg: none`) cannot pick a weaker check: anything that is
# not an accepted asymmetric algorithm is refused before a key is ever chosen.

# What a verified token has to say about itself. `aud` is the fixed value the
# Supabase auth server stamps on a signed-in user's token, and `iss` is that
# project's auth origin, so a valid token minted by a different Supabase project
# is rejected here rather than trusted for being well-formed.
JWT_AUDIENCE = "authenticated"
JWKS_PATH = "/auth/v1/.well-known/jwks.json"
# The one set of algorithms a token may be signed with, as a config constant so
# there is a single place that decides it. Asymmetric only on purpose (see the
# note above): HS256 and the shared secret are not here, so a token is only ever
# checked against a public key fetched from the project's JWKS.
ACCEPTED_ALGORITHMS = frozenset({"ES256", "RS256"})
# How long a fetched JWKS document is reused before it is fetched again. Signing
# keys rotate rarely, and the alternative (a fetch per request) would put a
# network round trip on the critical path of every single API call.
JWKS_LIFESPAN_SECONDS = 3600

AUTH_REQUIRED_MESSAGE = "Please sign in and try again."
# The one sentence every 401 returns. A server-side problem (no SUPABASE_URL, a
# JWKS document that could not be fetched) is still answered with exactly this,
# not a distinct "sign-in unavailable" sentence: a client that could tell a
# misconfigured deployment apart from a stale token would be reading the
# server's configuration state off the error, so the difference is collapsed
# here and the real reason goes only to the log.

# The account is real and the token is valid, but this principal is not permitted
# (anonymous, the wrong provider, or off the email allowlist). A 403 rather than
# a 401 because signing in again cannot fix it, and one sentence for every reason
# so it does not become an oracle for which rule was tripped.
NOT_PERMITTED_MESSAGE = "This account is not allowed here. Please sign in with an approved Google account."


class AuthenticatedUser(NamedTuple):
    """Who a verified token says is calling.

    `email` is "" when the token carries no email claim, which is possible and
    is not an error: `user_id` (the `sub` claim) is the identity, the email is
    only ever something to show the reader.
    """

    user_id: str
    email: str


class Unauthorized(Exception):
    """No usable token on this request.

    Carries the one sentence the caller is allowed to see. "expired", "signed by
    the wrong key" and "not a JWT at all" deliberately collapse into the same
    answer: telling them apart would help a caller probing for the shape of a
    valid token and helps a signed-out reader not at all.
    """

    def __init__(self, message: str = AUTH_REQUIRED_MESSAGE):
        super().__init__(message)


class Forbidden(Exception):
    """A verified token whose principal is not allowed to use this app.

    Separate from Unauthorized because the answer is different: the signature
    checked out and the session is real, so re-authenticating changes nothing.
    Anonymous sign-ins, a provider other than the allowed ones, and an email off
    the allowlist all raise this, and all carry the same sentence so it cannot be
    used to discover which rule refused the caller.
    """

    def __init__(self, message: str = NOT_PERMITTED_MESSAGE):
        super().__init__(message)


@app.exception_handler(Unauthorized)
async def _unauthorized_handler(request: Request, exc: Unauthorized) -> JSONResponse:
    """One shape for every rejection: {"message": "..."} with a 401.

    Not FastAPI's own HTTPException, whose body is {"detail": ...}: every other
    error this API produces says `message`, and the frontend reads that one key.
    `no-store` because a 401 must never be cached and replayed at a reader who
    has since signed in.
    """
    return JSONResponse(
        {"message": str(exc)},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer", "Cache-Control": "no-store"},
    )


@app.exception_handler(Forbidden)
async def _forbidden_handler(request: Request, exc: Forbidden) -> JSONResponse:
    """The same body shape as a 401, with a 403 and no WWW-Authenticate.

    403 rather than 401 says "authenticated, but not allowed": the browser must
    not respond by prompting for credentials again, so the challenge header the
    401 carries is deliberately absent. `no-store` for the same reason a 401 has
    it: a cached refusal must never outlive a change to who is permitted.
    """
    return JSONResponse(
        {"message": str(exc)},
        status_code=403,
        headers={"Cache-Control": "no-store"},
    )


# (base url, client). One PyJWKClient per process, built on first use and reused
# for the life of the instance, so the JWKS document is fetched once per cold
# start rather than once per request. Keyed on the URL it was built for so a
# changed SUPABASE_URL (a test, a rebound environment) cannot be served by a
# client still pointing at the old project.
_jwks_client: Optional[tuple] = None
_jwks_client_lock = threading.Lock()


def _supabase_base_url() -> str:
    """The project's origin, with no trailing slash.

    Read per call rather than at import: this module is imported once per cold
    start, and a test that rebinds the environment should be describing the
    world this function sees.
    """
    raw = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or ""
    return raw.strip().rstrip("/")


def _jwks_client_for(base_url: str) -> "jwt.PyJWKClient":
    global _jwks_client
    with _jwks_client_lock:
        if _jwks_client is None or _jwks_client[0] != base_url:
            _jwks_client = (
                base_url,
                jwt.PyJWKClient(
                    base_url + JWKS_PATH,
                    cache_keys=True,
                    lifespan=JWKS_LIFESPAN_SECONDS,
                ),
            )
        return _jwks_client[1]


# ── Authorization ─────────────────────────────────────────────────────────────
# Signature verification proves a token was minted by this project; authorization
# decides whether the person it names may use the app at all. The scope, set by
# env so it can change without a deploy, is: any Google account. Three env knobs
# express it, all read at call time so a test or a rebound deployment sees the
# world it set:
#
#   AUTH_ALLOWED_PROVIDERS       comma-separated, DEFAULTS to "google". The
#                                token's app_metadata.provider must be in the set.
#                                Email/password self-signup is a hole precisely
#                                because anyone can open an account, so the
#                                provider gate is what closes it; widening the set
#                                later (add "azure", say) is a config change.
#   AUTH_ALLOWED_EMAILS          comma-separated exact addresses.
#   AUTH_ALLOWED_EMAIL_DOMAINS   comma-separated domains.
#
# The email allowlist is only enforced when at least one of its two knobs is set;
# with both unset (the shipped default) any authenticated Google address is
# allowed, which is the decided scope. Anonymous sign-ins and any role other than
# "authenticated" are always refused, independent of every knob: Supabase gives
# anonymous users the "authenticated" role too, so is_anonymous is checked
# directly rather than inferred from the role.
_DEFAULT_ALLOWED_PROVIDERS = frozenset({"google"})


def _split_env_set(name: str) -> frozenset:
    """A comma-separated env var as a lowercased set, empty when unset."""
    raw = os.environ.get(name, "")
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _allowed_providers() -> frozenset:
    """The permitted OAuth providers. Defaults to {"google"} when the knob is
    unset; an explicitly empty value disables the provider gate."""
    if os.environ.get("AUTH_ALLOWED_PROVIDERS") is None:
        return _DEFAULT_ALLOWED_PROVIDERS
    return _split_env_set("AUTH_ALLOWED_PROVIDERS")


def _token_email(claims: dict) -> str:
    """The token's email, top-level first and then user_metadata, or "".

    Supabase stamps `email` at the top level of an access token; the copy under
    user_metadata is the fallback for a token shape that omits it. Never an
    error to have none: the address is only ever a label and an allowlist input.
    """
    email = claims.get("email")
    if isinstance(email, str) and email:
        return email
    metadata = claims.get("user_metadata")
    if isinstance(metadata, dict):
        nested = metadata.get("email")
        if isinstance(nested, str):
            return nested
    return ""


def _token_provider(claims: dict) -> str:
    """The lowercased app_metadata.provider, or "" when absent."""
    metadata = claims.get("app_metadata")
    if isinstance(metadata, dict):
        provider = metadata.get("provider")
        if isinstance(provider, str):
            return provider.strip().lower()
    return ""


def authorization_reason(
    claims: dict,
    *,
    allowed_providers: frozenset,
    allowed_emails: frozenset,
    allowed_domains: frozenset,
) -> Optional[str]:
    """Pure: None if this token's principal is permitted, else a short reason.

    A non-None result means 403. Kept free of os.environ so it can be unit-tested
    by handing it claims and allowlists directly; `_authorize_claims` is the thin
    wrapper that reads the env and turns a reason into a `Forbidden`.
    """
    if claims.get("is_anonymous") is True:
        return "anonymous"
    if claims.get("role") != "authenticated":
        return "role"
    if allowed_providers and _token_provider(claims) not in allowed_providers:
        return "provider"
    if allowed_emails or allowed_domains:
        email = _token_email(claims).lower()
        domain = email.rpartition("@")[2]
        if email in allowed_emails:
            return None
        if domain and domain in allowed_domains:
            return None
        return "email"
    return None


def _authorize_claims(claims: dict) -> None:
    """Raise Forbidden unless the verified token's principal is permitted."""
    reason = authorization_reason(
        claims,
        allowed_providers=_allowed_providers(),
        allowed_emails=_split_env_set("AUTH_ALLOWED_EMAILS"),
        allowed_domains=_split_env_set("AUTH_ALLOWED_EMAIL_DOMAINS"),
    )
    if reason is not None:
        # The reason is for the log, never the client: the 403 body is one fixed
        # sentence so it cannot be probed to learn which rule refused the caller.
        logger.info("token refused by authorization: %s", reason)
        raise Forbidden()


def verify_bearer_token(token: str) -> AuthenticatedUser:
    """Decode and fully verify one Supabase access token, or raise Unauthorized."""
    base_url = _supabase_base_url()
    if not base_url:
        # A server-side misconfiguration, logged as one, but answered with the
        # same 401 sentence a stale token gets: the caller learns nothing about
        # the deployment's configuration state (see AUTH_REQUIRED_MESSAGE).
        logger.error("SUPABASE_URL is not set, so no access token can be verified")
        raise Unauthorized()

    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise Unauthorized() from None

    algorithm = header.get("alg")
    if algorithm not in ACCEPTED_ALGORITHMS:
        # HS256 (the retired shared secret), `alg: none`, and any unknown value
        # all land here and are refused before a key is chosen, so no forged
        # token can steer verification onto a weaker check.
        raise Unauthorized()

    try:
        key = _jwks_client_for(base_url).get_signing_key_from_jwt(token).key
    except jwt.PyJWTError:
        # A token naming a key this project does not publish. That is a bad
        # token, not a broken server.
        raise Unauthorized() from None
    except Exception:  # noqa: BLE001 - a JWKS fetch is a network call
        # A network problem reaching the JWKS document is server-side, but the
        # caller still sees only the one sentence; the real reason goes to the log.
        logger.exception("could not fetch the Supabase JWKS document")
        raise Unauthorized() from None

    try:
        claims = jwt.decode(
            token,
            key,
            # Pinned to the single algorithm the header named, which is already
            # known to be in ACCEPTED_ALGORITHMS: an asymmetric public key can
            # never be misused as an HS256 secret here.
            algorithms=[algorithm],
            audience=JWT_AUDIENCE,
            issuer=base_url + "/auth/v1",
            # `require` rather than trusting the defaults: a token with no `exp`
            # would otherwise verify and never expire, and one with no `sub` has
            # no identity to rate-limit or attribute a report to.
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError:
        raise Unauthorized() from None

    user_id = claims.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise Unauthorized()

    # Signature and standard claims are good; now authorization. A validly signed
    # token is not the same as a permitted one: an anonymous sign-in, an
    # email/password account, or an address off the allowlist all get this far
    # and are refused here with a 403, not a 401.
    _authorize_claims(claims)

    return AuthenticatedUser(user_id, _token_email(claims))


def authenticate_request(request: Request) -> AuthenticatedUser:
    """The whole check for one request: find the bearer token, then verify it."""
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise Unauthorized()
    return verify_bearer_token(token.strip())


async def require_user(request: Request) -> AuthenticatedUser:
    """The dependency every route below carries.

    It reaches `authenticate_request` through the module rather than closing over
    it, and that is the point rather than a stylistic choice: FastAPI captured
    *this* function when the routes were declared, so the only way tests can
    stand in a fake authenticated user for the whole existing suite is if the
    thing they replace is looked up at call time.
    """
    return authenticate_request(request)


# Written once and shared, so a new route cannot be added with a subtly
# different dependency, or with none at all by omission.
CurrentUser = Depends(require_user)


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
# The per-user budgets above follow the person, which is right for a normal
# account but multiplies without bound if one attacker opens N accounts behind a
# single address: N times the per-user cost, for free. So every per-user budget
# also has a per-IP ceiling, a higher cap that every account behind one address
# shares. It is a multiple of the per-user limit rather than an absolute so a
# deployment only tunes one number; the multiple is the abuse floor, not the
# normal path, so a handful of colleagues in one office never feel it.
RATE_LIMIT_IP_CEILING_MULTIPLIER = _rate_limit_env("RATE_LIMIT_IP_CEILING_MULTIPLIER", 5)
RATE_LIMIT_WINDOW_SECONDS = 3600

_rate_limit_lock = threading.Lock()
_rate_limit_hits: dict = defaultdict(deque)


def _client_ip(request: Request) -> str:
    """The caller's address, taken so it cannot be spoofed on Vercel.

    Vercel overwrites X-Forwarded-For with the real client address and does not
    forward a client-supplied one (per Vercel's request-headers docs), so the
    trustworthy value is the one the platform appended: the RIGHTMOST entry, not
    the leftmost. Reading the first entry would trust whatever a caller chose to
    put there and let the per-IP ceiling be evaded by rotating a fake prefix.
    X-Real-IP (also Vercel-set) is the fallback, then the socket peer for local
    runs where no proxy header exists at all.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        if parts:
            return parts[-1]
    real_ip = request.headers.get("x-real-ip", "").strip()
    if real_ip:
        return real_ip
    client = request.client
    return client.host if client else "unknown"


def _rate_limit_identity(request: Request, user: Optional[AuthenticatedUser]) -> str:
    """Which per-user budget this call spends from.

    The signed-in user, so a limit follows the person rather than the network: an
    office behind one NAT address used to share a single budget between everyone
    in it, and a caller who wanted a fresh one only had to change address. The IP
    path stays as the floor for a request with no user attached. Nothing reaches
    it today, because every route requires a user, and that is exactly why it is
    kept: it is what an intentionally public route would fall back to, and
    deleting it would make adding one a rate-limiting hole.
    """
    if user is not None and user.user_id:
        return "user:{}".format(user.user_id)
    return "ip:{}".format(_client_ip(request))


def _ip_ceiling(limit: int) -> int:
    """The per-IP cap that corresponds to a per-user `limit`."""
    return max(limit, limit * RATE_LIMIT_IP_CEILING_MULTIPLIER)


def _window_count(key: str, now: float) -> int:
    """Drop this key's hits older than the window and return how many remain.
    Caller must hold `_rate_limit_lock`."""
    hits = _rate_limit_hits[key]
    while hits and now - hits[0] > RATE_LIMIT_WINDOW_SECONDS:
        hits.popleft()
    return len(hits)


def _rate_limited(
    request: Request, user: Optional[AuthenticatedUser], bucket: str, limit: int
) -> bool:
    """True once this call would exceed either the per-user budget for `bucket`
    or the per-IP ceiling that caps every account behind one address.

    Both gates share one sliding window. The call is allowed only when it is
    under the per-user limit AND under the per-IP ceiling, and it is recorded
    against both only when allowed, so a call rejected by one gate never fills
    the other. The ceiling is a distinct gate only for a signed-in caller: a
    request with no user already spends from its address (see
    `_rate_limit_identity`), and adding a second address bucket would just limit
    it twice over.
    """
    identity = _rate_limit_identity(request, user)
    gates = [("{}:{}".format(bucket, identity), limit)]
    if user is not None and user.user_id:
        gates.append(("{}:ip:{}".format(bucket, _client_ip(request)), _ip_ceiling(limit)))
    now = time.monotonic()
    with _rate_limit_lock:
        if any(_window_count(key, now) >= cap for key, cap in gates):
            return True
        for key, _cap in gates:
            _rate_limit_hits[key].append(now)
        return False


def _save_report(query: str, profile: str, bundle, user_id: Optional[str] = None) -> Optional[str]:
    """Store a finished run and return the id it can be reached at, or None.

    Persistence is a bonus on top of a run that already succeeded, never a
    condition of it: a database that is down, unconfigured (every local dev
    machine without `.env.local`) or simply slow costs the reader a share link
    and nothing else, so every failure here is one warning line and a None.
    The bundle itself is already on its way to the browser either way.

    `user_id` records who ran it. It is only ever attribution: reading a saved
    report is not restricted to its owner (see `get_saved_report`), because a
    share link is meant to be shareable with anyone who can sign in.
    """
    if not isinstance(bundle, dict):
        return None
    report_id = secrets.token_urlsafe(REPORT_ID_BYTES)
    try:
        db.save_report(report_id, query, profile, bundle, user_id=user_id)
    except db.StorageUnavailable as exc:
        logger.warning("report not saved: %s", exc)
        return None
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unexpected failure while saving a report")
        return None
    return report_id


def _set_run_result_durably(
    run_id: str,
    status: str,
    report_id: Optional[str] = None,
    error: Optional[str] = None,
) -> bool:
    """Write a run's terminal status, retrying a transient database blip.

    Unlike a progress flush, the terminal write is not something to lose quietly:
    if it does not land, the run stays `running` and the stale-heartbeat reaper
    re-runs a pipeline that already finished. So this retries a bounded number of
    times with a short sleep, and only when every attempt has failed does it give
    up - loudly, in the log, and by returning False rather than raising, so the
    caller (and the worker route above it) treats the run as the completed thing
    it is instead of surfacing a 500 that would invite yet another execution.
    """
    for attempt in range(1, TERMINAL_WRITE_ATTEMPTS + 1):
        try:
            db.set_run_result(run_id, status, report_id=report_id, error=error)
            return True
        except Exception:  # noqa: BLE001 - StorageUnavailable and any surprise alike
            if attempt >= TERMINAL_WRITE_ATTEMPTS:
                logger.error(
                    "could not record terminal status %r for run %s after %d attempts; "
                    "the run finished but its row may be reaped and retried",
                    status,
                    run_id,
                    TERMINAL_WRITE_ATTEMPTS,
                )
                return False
            time.sleep(TERMINAL_WRITE_RETRY_SECONDS)
    return False


def _execute_run(run: dict) -> None:
    """Run one claimed row to a terminal state, in the worker invocation.

    The old design ran pipeline.run_report in a background thread and streamed
    its progress to a browser that had to stay connected; a client disconnect
    was the cancel signal, and a closed tab killed the run. This is the queued
    replacement: `run` is a row `claim_next_run` already flipped to `running`,
    and this function takes it the rest of the way, writing exactly one terminal
    result (`succeeded`, `failed`, or `cancelled`). Nothing here depends on a
    client being connected, so a closed tab is a no-op server-side.

    Progress is coalesced rather than streamed: strings accumulate in memory and
    are flushed to the row at most every PROGRESS_FLUSH_SECONDS (and once more
    before the terminal write), which doubles as the heartbeat the stale-run
    reaper reads. Cancellation is a small daemon poller that watches
    `db.is_cancelling` and sets the same threading.Event the engine honours at
    stage boundaries, so a Cancel press stops the run between stages and raises
    RunCancelled. run_report is blocking and can take minutes, which is exactly
    why this is reached from a plain `def` worker route (run in Starlette's
    threadpool) and not from the event loop.
    """
    run_id = run["id"]
    user_id = run.get("user_id")
    query = run.get("query") or ""
    profile = run.get("profile") or ""
    inputs = run.get("inputs") or {}

    cancel_event = threading.Event()
    deadline = time.monotonic() + REPORT_SOFT_DEADLINE_SECONDS

    progress_log: list = []
    last_flush = {"at": 0.0}

    def flush_progress(force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - last_flush["at"] < PROGRESS_FLUSH_SECONDS:
            return
        last_flush["at"] = now
        try:
            db.update_run_progress(run_id, list(progress_log))
        except Exception:  # noqa: BLE001 - progress is best effort, never fatal
            logger.exception("could not flush run progress for %s", run_id)

    def progress(message: str) -> None:
        progress_log.append(message)
        flush_progress()

    stop_poll = threading.Event()

    def poll_cancel() -> None:
        # `wait` rather than `sleep` so the finally below can wake the poller
        # immediately once the run is done, instead of it sitting out one last
        # interval. It returns True only when stop_poll is set, which is the
        # signal to stop watching.
        while not stop_poll.wait(CANCEL_POLL_SECONDS):
            try:
                if db.is_cancelling(run_id):
                    cancel_event.set()
                    return
            except Exception:  # noqa: BLE001 - a failed poll must not kill the run
                logger.exception("could not poll cancellation for %s", run_id)

    poller = threading.Thread(target=poll_cancel, daemon=True)
    poller.start()

    try:
        run_kwargs = dict(
            days=inputs.get("days"),
            subreddits=inputs.get("subreddits"),
            relevance_filter=inputs.get("relevance_filter"),
            progress=progress,
        )
        if _accepts_kwarg(pipeline.run_report, "content_types"):
            run_kwargs["content_types"] = list(inputs.get("content_types") or [])
        elif _accepts_kwarg(pipeline.run_report, "content_type"):
            # An engine from before the setting took several values: it can still
            # honour one, and one is what the overwhelming majority of runs pick.
            run_kwargs["content_type"] = (inputs.get("content_types") or [None])[0]
        if _accepts_kwarg(pipeline.run_report, "channels"):
            run_kwargs["channels"] = list(inputs.get("channels") or [])
        if _accepts_kwarg(pipeline.run_report, "phrases"):
            run_kwargs["phrases"] = list(inputs.get("phrases") or [])
        if _accepts_kwarg(pipeline.run_report, "cancel"):
            run_kwargs["cancel"] = cancel_event
        if _accepts_kwarg(pipeline.run_report, "deadline"):
            run_kwargs["deadline"] = deadline

        try:
            bundle = pipeline.run_report(query, profile, **run_kwargs)
        finally:
            # Always land the last progress the engine reported, whatever the
            # outcome, so the row a reader polls matches where the run actually got.
            flush_progress(force=True)
    except pipeline.RunCancelled:
        # A user pressing Cancel is the system working, not an incident: no
        # traceback, and a terminal `cancelled` rather than a failure.
        logger.info("run cancelled: %s", run_id)
        _set_run_result_durably(run_id, "cancelled")
        return
    except (pipeline.RunError, ValueError) as exc:
        # These already carry a user-ready sentence (see pipeline.py and
        # analysis.py's error mapping), so it is passed straight through.
        _set_run_result_durably(run_id, "failed", error=str(exc))
        return
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        # The genuine last resort still logs a traceback, exactly as the old
        # streaming path did, and the reader sees only the generic sentence.
        logger.exception("unhandled error while building a report for %s", run_id)
        _set_run_result_durably(run_id, "failed", error=GENERIC_ERROR_MESSAGE)
        return
    finally:
        # Stop the poller whatever happened; it is a daemon, but leaving it
        # spinning against a finished run is a round trip nobody needs.
        stop_poll.set()

    # The bundle is saved the same server-side, best-effort way it always was
    # (see _save_report): a storage miss costs the reader a share link, not the
    # run, so a None report_id still lands as a `succeeded` terminal.
    report_id = _save_report(query, profile, bundle, user_id)
    _set_run_result_durably(run_id, "succeeded", report_id=report_id)


@app.post("/api/report")
async def post_report(request: Request, user: AuthenticatedUser = CurrentUser):
    """Create a queued run and return its id, without doing the work here.

    This used to stream the whole run as NDJSON while a worker thread built it;
    now it validates, mints an id, writes one `queued` row, and returns 202 with
    that id. The browser polls GET /api/runs/{id} and an external minute-cron
    drives POST /api/run-worker, which is what actually executes the run. A
    validation failure comes back as the same {"type": "invalid", ...} body the
    old terminal line carried, just as plain JSON rather than a one-line stream.
    """
    origin_error = _reject_cross_origin(request)
    if origin_error is not None:
        return origin_error

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any body that is not valid JSON
        return _invalid_response(["Send a JSON body with query and profile."])

    if not isinstance(body, dict):
        return _invalid_response(["Send a JSON body with query and profile."])

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
        return _invalid_response(checked["errors"])

    if _rate_limited(request, user, "report", REPORTS_PER_HOUR):
        return JSONResponse(
            {"message": "Too many report runs on this account in the last hour. Please try again later."},
            status_code=429,
        )

    chosen_types = checked.get("content_types")
    if chosen_types is None:
        # An engine that still validates one content type at a time.
        legacy = checked.get("content_type")
        chosen_types = [legacy] if legacy else []

    # The whole run request, frozen onto the row so the worker reconstructs it
    # without the browser being involved again. `.get(...) or []` rather than
    # `checked["..."]` for the same reason the old boundary used it: an engine
    # from before phrases or channels returns no such key, and version skew must
    # not be a KeyError here.
    inputs = {
        "days": checked["days"],
        "subreddits": checked["subreddits"],
        "relevance_filter": checked["relevance_filter"],
        "content_types": chosen_types,
        "phrases": checked.get("phrases") or [],
        "channels": checked.get("channels") or [],
    }

    run_id = secrets.token_urlsafe(REPORT_ID_BYTES)
    try:
        db.create_run(run_id, user.user_id, checked["query"], checked["profile"], inputs)
    except db.StorageUnavailable as exc:
        # No durable queue means no run: unlike a saved report, a run cannot be a
        # best-effort bonus, so this surfaces as a clean 503 the frontend shows.
        return JSONResponse({"message": str(exc)}, status_code=503)
    except Exception:  # noqa: BLE001 - last resort, details go to the log only
        logger.exception("unexpected failure while creating a run")
        return JSONResponse({"message": db.UNAVAILABLE_MESSAGE}, status_code=503)

    return JSONResponse({"run_id": run_id}, status_code=202)


# ── The worker entrypoint ────────────────────────────────────────────────────────
# Driven by an external minute-cron (Supabase pg_cron calling this route through
# pg_net), not by a browser and not by Vercel Cron (the project is on Hobby). It
# is the one route in this file that does NOT carry require_user: pg_net cannot
# hold a Supabase session, so it authenticates with a shared CRON_SECRET instead,
# and it is listed in the test suite's PUBLIC_API_ROUTES precisely so that
# exemption is deliberate rather than an accident.


def _reject_worker_caller(request: Request) -> Optional[JSONResponse]:
    """None if this request carries the cron secret, else a 401 JSONResponse.

    Fails closed when CRON_SECRET itself is unset: a deployment that forgot to
    configure the secret refuses every call rather than running the worker open.
    The comparison is constant-time so a caller cannot learn the secret a byte at
    a time from response timing, and every failure returns the one collapsed
    sentence so the route is not an oracle for which check tripped.
    """
    secret = os.environ.get("CRON_SECRET", "")
    if not secret:
        logger.error("CRON_SECRET is not set, so the worker route refuses every call")
        return JSONResponse({"message": CRON_AUTH_FAILED_MESSAGE}, status_code=401)

    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token.strip(), secret):
        return JSONResponse({"message": CRON_AUTH_FAILED_MESSAGE}, status_code=401)
    return None


@app.post("/api/run-worker")
def post_run_worker(request: Request):
    """Claim and execute exactly one run per invocation.

    One per invocation is deliberate, not a throttle: a single run can take most
    of the 285s budget, and a worker that tried to drain the queue in a loop
    would blow past Vercel's 300s maxDuration on the second one. Overlapping
    minute-ticks are safe because claim_next_run uses FOR UPDATE SKIP LOCKED, so
    two workers racing either take different rows or one takes nothing. This is a
    plain `def` (not `async def`) on purpose: pipeline.run_report blocks for
    minutes, and Starlette runs a sync route in a threadpool, keeping the event
    loop free instead of freezing it for the whole run.
    """
    auth_error = _reject_worker_caller(request)
    if auth_error is not None:
        return auth_error

    try:
        # Durable safety net first, before anything is claimed: end runs that can
        # no longer make progress (a `running` row retried up to the cap, or a
        # `queued` row no worker ever picked up) so neither the retry storm nor a
        # forever-queued run outlives this tick. See db.fail_stalled_runs.
        swept = db.fail_stalled_runs()
        if swept:
            logger.info("failed %d stalled run(s) before claiming", swept)
        claim = db.claim_next_run()
    except db.StorageUnavailable as exc:
        return JSONResponse({"message": str(exc)}, status_code=503)

    if claim is None:
        return JSONResponse({"ran": None})

    _execute_run(claim)
    return JSONResponse({"ran": claim["id"]})


# ── Run status and control ───────────────────────────────────────────────────────
# All three require_user, and a run is private to whoever started it: a row that
# belongs to someone else is a 404, the same as one that does not exist, because
# a run (unlike a shareable report) is nobody else's business. Every id is shape-
# checked against REPORT_ID_PATTERN before the database is touched, exactly as a
# report id is.


@app.get("/api/runs")
async def get_runs(user: AuthenticatedUser = CurrentUser):
    """The caller's own runs, newest first. `no-store` because this is a private,
    per-user view whose freshness matters (a run's status changes as it runs)."""
    headers = {"Cache-Control": "no-store"}
    try:
        runs = db.list_runs(user.user_id)
    except db.StorageUnavailable as exc:
        return JSONResponse({"message": str(exc)}, status_code=503, headers=headers)
    return JSONResponse({"runs": runs}, headers=headers)


@app.get("/api/runs/{run_id}")
async def get_run_status(run_id: str, user: AuthenticatedUser = CurrentUser):
    """One run's live state, for the browser to poll. `no-store` so a poll always
    reads the real status rather than a cached one, and owner-checked so a run id
    guessed or leaked cannot be read by anyone but the person who started it."""
    headers = {"Cache-Control": "no-store"}
    if not REPORT_ID_PATTERN.match(run_id):
        return JSONResponse({"message": RUN_NOT_FOUND_MESSAGE}, status_code=404, headers=headers)

    try:
        row = db.get_run(run_id)
    except db.StorageUnavailable as exc:
        return JSONResponse({"message": str(exc)}, status_code=503, headers=headers)

    if row is None or row.get("user_id") != user.user_id:
        return JSONResponse({"message": RUN_NOT_FOUND_MESSAGE}, status_code=404, headers=headers)

    return JSONResponse(
        {
            "status": row.get("status"),
            "progress": row.get("progress"),
            "report_id": row.get("report_id"),
            "error": row.get("error"),
        },
        headers=headers,
    )


@app.post("/api/runs/{run_id}/cancel")
async def post_cancel_run(run_id: str, request: Request, user: AuthenticatedUser = CurrentUser):
    """Ask a run to stop. Owner-checked in one query (see db.mark_run_cancelling):
    a run that is not the caller's, or is already terminal, flips nothing and is a
    404, so this is never an oracle for whether a given id exists for someone else.
    The worker's poller sees the flag at the next stage boundary and stops."""
    headers = {"Cache-Control": "no-store"}
    if not REPORT_ID_PATTERN.match(run_id):
        return JSONResponse({"message": RUN_NOT_FOUND_MESSAGE}, status_code=404, headers=headers)

    try:
        flipped = db.mark_run_cancelling(run_id, user.user_id)
    except db.StorageUnavailable as exc:
        return JSONResponse({"message": str(exc)}, status_code=503, headers=headers)

    if not flipped:
        return JSONResponse({"message": RUN_NOT_FOUND_MESSAGE}, status_code=404, headers=headers)

    return JSONResponse({"status": "cancelling"}, headers=headers)


@app.get("/api/reports/{report_id}")
async def get_saved_report(report_id: str, user: AuthenticatedUser = CurrentUser):
    """One saved report and every draft written against it.

    Any signed-in reader holding the link may open it, whoever ran it: that is
    the product decision behind share links, so `user` is required here (a
    signed-out visitor gets nothing) but is deliberately not compared against
    the report's own `user_id`.

    Shape: {"bundle": {...}, "drafts": {"<idea_index>": "<post>", ...}}. The id
    in the path is the only credential beyond a session, so this is deliberately
    uncacheable:
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
async def get_sample(user: AuthenticatedUser = CurrentUser):
    payload = _load_sample_payload()
    # Only the hit is cacheable. A miss is usually transient from the caller's
    # side (a sample not deployed yet, a version the file has since caught up
    # with), and an hour-long cache on it means the frontend's "try again in a
    # moment" is a promise the browser will not honour: the retry is served from
    # cache and fails identically until the entry expires. That is now a
    # user-facing path rather than a dev-only one, so the miss is no-store.
    #
    # `private`, not `public`: this route requires a session now, and Vercel's
    # CDN sits in front of this function and will cache a `public` response. One
    # signed-in reader's request would then be enough to leave a copy of the
    # sample in a shared cache that anonymous requests could be served from,
    # which is the gate coming off for exactly the route that advertises the
    # product. The body is identical for every reader either way, so nothing is
    # lost but the shared hit.
    cache = "private, max-age=3600" if payload.get("available") else "no-store"
    return JSONResponse(payload, headers={"Cache-Control": cache})


@app.get("/api/sample/available")
async def get_sample_available(user: AuthenticatedUser = CurrentUser):
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
async def post_draft(request: Request, user: AuthenticatedUser = CurrentUser):
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

    if _rate_limited(request, user, "draft", DRAFTS_PER_HOUR):
        return JSONResponse(
            {"message": "Too many drafts on this account in the last hour. Please try again later."},
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
        # RunError/ValueError branch in _execute_run.
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
async def post_save_draft(request: Request, user: AuthenticatedUser = CurrentUser):
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

    if _rate_limited(request, user, "draft-save", DRAFT_SAVES_PER_HOUR):
        return JSONResponse(
            {"message": "Too many draft saves on this account in the last hour. Please try again later."},
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
async def post_export(kind: str, request: Request, user: AuthenticatedUser = CurrentUser):
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
