# Content Ideas Pivot — Contract Spec (v2)

> **Historical document.** This spec describes bundle v2. Since then, v3 removed
> the platform input and v4 removed the time-window input (the app always fetches
> the last 30 days) and replaced newest-first sampling with relevance-triaged
> selection (`analysis.rank_items` / `analysis.rate_relevance`). Where this
> document and the code disagree, the code and README are current.

The app pivots from "brand research over manually-picked subreddits" to
"content-idea generation from a free-text query, with automatic subreddit
discovery". This document is the single source of truth for the new inputs,
the discovery module, the synthesis output, and the v2 report bundle. Every
module and every test conforms to this; nothing here is advisory.

## Product flow

1. User types **what they're looking for** (one text box), optionally **who
   they are**, picks a **platform** (LinkedIn / Instagram / both) and a time
   window. No subreddit input anywhere.
2. The app asks the model for candidate subreddits, validates them against the
   Arctic Shift subreddit search, and keeps the best 4.
3. Existing fetch → classify → synthesize pipeline runs over those communities.
4. The report is centred on **platform-native content ideas** (8–12, each with
   a hook), backed by topics, pain points, questions and citations. The
   brand-mentions section is removed entirely.

## Inputs

| field      | rules                                                        |
|------------|--------------------------------------------------------------|
| `query`    | required, 10–300 chars. "What do you want content ideas about?" |
| `profile`  | optional, ≤500 chars. "About you / your brand" — tailors ideas. |
| `platform` | one of `linkedin`, `instagram`, `both`                       |
| `days`     | one of 7, 14, 30 (default 7)                                 |

`pipeline.PLATFORMS = {"linkedin": "LinkedIn", "instagram": "Instagram",
"both": "LinkedIn + Instagram"}` — keys are stored in the bundle, labels are
rendered. `MAX_QUERY_CHARS = 300`, `MIN_QUERY_CHARS = 10`,
`MAX_PROFILE_CHARS = 500`.

## Discovery — new module `discovery.py`

Public API:

```python
discover(query, profile, *, chat=None, lookup=None) -> dict
# returns {"communities": [{"name", "reason", "subscribers", "checked"}],
#          "considered": ["name", ...]}   # every candidate the model proposed
# raises ValueError with a user-friendly message when nothing usable remains
```

- One Groq call (JSON mode) via `analysis.chat_json` (see below) proposing
  8–12 candidates: `{"candidates": [{"name": "...", "reason": "..."}]}`.
  Reason ≤ 15 words, written for the end user ("Freelancers swap pricing and
  client-management advice here").
- Sanitisation mirrors the codebase's trust-nothing style: each name goes
  through `reddit_data.normalize_subreddit` (invalid → dropped), dedupe
  case-insensitively, `analysis.strip_markdown` on reasons, cap at
  `MAX_CANDIDATES = 12`.
- Each candidate is checked with `reddit_data.lookup_subreddit` (new, below).
  Drop: nonexistent, private, over18. Keep model's order (it ranks by
  relevance); take the first `MAX_COMMUNITIES = 4` that pass.
- **Fail-open**: if the lookup service itself is down for every candidate,
  keep the model's first 4 valid-looking names with `checked: False` and let
  the fetch stage decide — same philosophy as `validate_subreddit`.
- `chat` and `lookup` kwargs are injection points for tests and Streamlit
  caching, exactly like the callables `pipeline.run_report` already takes.

## `reddit_data.py` — one addition

```python
lookup_subreddit(name) -> Optional[dict]
# {"name": canonical, "subscribers": int, "over18": bool, "public": bool}
# None when the community does not exist.
# Raises RuntimeError when the lookup service itself failed (so discovery can
# tell "not found" apart from "couldn't check").
```

Built on the existing `_search_subreddits` / `SUBREDDITS_ENDPOINT` (exact
`subreddit` param). No other changes to this module.

## `analysis.py` changes

- Expose `chat_json(prompt, max_tokens, label)` — public alias of `_chat_json`
  so discovery can use the same retry/fallback/error machinery.
- `synthesize(items, topics, query, profile, platform, subreddits,
  preview_chars)` — signature replaces `brand_name`/`brand_description`.
- `MAX_CONTENT_IDEAS = 12`; the prompt asks for 8–12 ideas.
- Synthesis prompt reframe: "You are a content strategist for someone growing
  an audience on {platform label}. They are researching: {query}." Include
  profile line only when non-empty. Executive summary = the most important
  takeaway *for their content strategy*.
- Each content idea gains two fields:
  - `"hook"` — the literal opening line of the post/script, ≤120 chars,
    written to stop the scroll. Required.
  - `"platform"` — `"linkedin"` or `"instagram"`. When the run platform is
    fixed, every idea uses it; when `both`, the model assigns per idea.
- Formats are platform-native and the prompt lists them explicitly:
  LinkedIn → Text post, Carousel (PDF), Poll, Long-form article, Video script.
  Instagram → Reel script, Carousel, Story series, Caption + image idea.
- `sanitize_synthesis` sanitises `hook` (strip_markdown, may be empty string
  if model omits it) and coerces `platform` to the allowed set, defaulting to
  the run's platform (passed in as a new arg or defaulted after the call —
  implementer's choice, but the returned dict is always valid).
- Classification call, sample fitting, token budgeting: unchanged.

## `pipeline.py` — v2

- `validate_inputs(query, profile, platform, days) -> dict` — same
  all-errors-at-once pattern; returns cleaned values.
- `run_report(query, profile, platform, days, progress=None, discover=None,
  fetch_posts=None, fetch_comments=None, classify=None, synthesize=None)`.
  Discovery is an injection point like the others.
- Flow: progress("Finding communities talking about this…") → `discover()` →
  progress("Found r/a, r/b, r/c — fetching posts…") → fetch each (existing
  per-community skip/notice behaviour; a community that returns no posts gets
  a notice, not a failure) → classify → synthesize → bundle.
- Discovery communities were already existence-checked, so the per-community
  `validate_subreddit` call is skipped (except for `checked: False` entries,
  where fetch errors handle it).
- If every discovered community yields nothing: RunError with a message that
  names the communities tried.

### Bundle v2 shape (only the deltas from v1)

```
bundle_version: 2
inputs: {query, profile, platform, platform_label, subreddits, days}
discovery: {communities: [{name, reason, subscribers, used: bool}],
            considered: [names]}          # NEW top-level section
analysis.content_ideas[*]: + hook, + platform    # per idea
stats: brand_mentions REMOVED
```

Everything else (sample, posts, comments, items, topics, sentiment,
engagement, date_range, lag_hours, trend, notices, model fields) is unchanged.
`run_label(bundle)` → `"{query truncated to 30 chars} · {platform_label} ·
{n}d"`.

## `stats.py`

Delete `brand_mentions` and anything only it used. No other changes.

## `report_export.py`

- Markdown report: header shows query + platform + communities; new
  "Communities analysed" section with each community's reason and subscriber
  count; brand-mentions section removed; each idea renders hook (bold, quoted)
  and platform.
- `ideas_csv`: add `hook` and `platform` columns.
- `filename_stem`: slug of the query (first ~40 chars) + date, e.g.
  `content-ideas-ai-automation-2026-07-26`.

## `app.py`

- Title: **Reddit Content Ideas** — caption: "Type what you're looking for.
  We find the Reddit communities, read them, and turn what people actually
  say into content ideas for your platform."
- Form: query text_area, profile text_area (marked optional), platform
  selectbox, window selectbox. No subreddit field.
- Landing "How it works": 1. Describe what you're looking for → 2. We find
  and read the communities → 3. Get platform-ready ideas with evidence.
- Report adds a "Communities analysed" section (name linked to
  reddit.com/r/name, reason, subscriber count, whether it contributed data)
  right after the summary chips; brand-mentions rendering removed; idea cards
  show the hook prominently (quoted) and a platform badge.
- Query params: `q`, `platform`, `days`.
- `load_sample_report` returns None unless `bundle_version == 2` (old sample
  hides gracefully until regenerated).
- Keep: fragments, cache wrappers, history, error-banner lifecycle, export
  buttons — all existing UX mechanics.

## `scripts/run_report.py`

Args: `--query`, `--profile`, `--platform`, `--days`, `--json`, `--markdown`,
`--sample`, plus `--subreddits` as an optional override that skips discovery
(for deterministic regeneration and debugging).

## Tests (all offline, fakes for HTTP and model)

- `tests/test_discovery.py` (new): candidate sanitisation (invalid names,
  dupes, markdown in reasons), lookup filtering (missing/private/over18),
  fail-open path, empty-result ValueError.
- `test_reddit_data.py`: add `lookup_subreddit` cases (found, not found,
  service down).
- `test_analysis.py`: synthesis sanitisation covers hook + platform coercion;
  prompt contains query/platform/profile; profile omitted when empty.
- `test_pipeline_and_export.py`: rewrite around v2 — injected fake discovery,
  bundle shape assertions, notices when a community is empty, RunError when
  all are, exporter columns/sections.
- `test_stats.py`: brand-mention tests removed.
- `test_app_state.py`: updated for new form/session fields.
- Sample-report shape test: assert against v2 once the sample is regenerated.

## Out of scope

Auth, persistence, scheduling, posting to platforms, non-Reddit sources.

## Addendum (2026-07-26): provider switch, design pass, em-dash policy

User-requested changes after the pivot landed:

### 1. LLM provider: Groq → Gemini on Vertex AI

Groq's free-tier daily token caps block real usage. The model layer moves to
Gemini via the `google-genai` SDK with `vertexai=True`.

- Auth: Application Default Credentials (already present on this machine);
  project comes from `GOOGLE_CLOUD_PROJECT` (this machine: `dutch-app-gd`),
  location from `GOOGLE_CLOUD_LOCATION` (default `global`).
- Models: `GEMINI_MODEL` env, default `gemini-2.5-flash`; fallback
  `gemini-2.5-flash-lite` on model-not-found, mirroring the existing
  PRIMARY/FALLBACK pattern. Display names like "Gemini 2.5 Flash on Vertex AI".
- `chat_json` keeps its signature `(prompt, max_tokens, label) -> (dict,
  model)` so discovery and both calls are untouched above the client layer.
- JSON mode via `response_mime_type="application/json"`, temperature 0.
- Token budget: the ladder/refit architecture stays (it is good protection),
  but the default budget rises (Gemini takes ~1M context);
  `GEMINI_TOKEN_BUDGET` env replaces `GROQ_TOKEN_BUDGET` (default 200000).
  The Groq header-learning probe (`ensure_token_limit`) is removed or reduced
  to a no-op; refit-on-payload-rejection stays.
- Error mapping keeps the same user-facing sentences: quota/429 → busy
  message, auth/credential failures → a message telling the user to run
  `gcloud auth application-default login` and set the project, payload too
  large → PayloadTooLarge refit.
- requirements.txt: `groq` out, `google-genai` in. `.env.example` and README
  setup rewritten for Vertex.

### 2. Em dashes: banned everywhere a user can see

- No em/en dash in any user-facing string: app copy, error messages,
  notices, prompts' example text, README, exports.
- Prompts gain an explicit rule: never use em dashes in any output string.
- `strip_markdown` (the shared model-output sanitiser) normalises them
  deterministically: " — " / " – " (spaced) → ", "; remaining "—"/"–" → "-".
  This guarantees generated hooks/ideas ship clean even if the model slips.
- Code comments are not user-facing and are exempt.

## Addendum 2 (2026-07-26): Streamlit removed, Next.js + FastAPI on Vercel

The user is deploying on Vercel. Streamlit is removed entirely. The Python
engine (reddit_data, discovery, analysis, pipeline, stats, report_export) is
UNCHANGED and stays at repo root; a FastAPI layer exposes it and a Next.js
app renders it. The report bundle v2 shape is untouched and remains the
single unit of state.

### Repo layout (target)

```
api/index.py          FastAPI app (Vercel Python runtime, Fluid compute)
*.py                  engine modules, unchanged, at root
app/, components/,    Next.js 15 App Router (TypeScript, Tailwind,
lib/, public/         shadcn/ui) at repo root
vercel.json           functions config (maxDuration, includeFiles), rewrites
package.json          Next.js deps and scripts
requirements.txt      engine + fastapi (streamlit/altair/pandas removed if
                      nothing but app.py used them)
scripts/run_report.py CLI stays (no streamlit dependency)
app.py, .streamlit/   DELETED in the cleanup phase (after frontend verified)
tests/test_app_state.py DELETED; replaced by tests/test_api.py
```

### API contract (bundle v2 everywhere; JSON; no auth)

- `POST /api/report` body `{query, profile, platform, days}`.
  Response: NDJSON stream (`application/x-ndjson`), one JSON object per line:
  `{"type": "progress", "message": "..."}` for each pipeline progress call,
  then exactly one terminal line:
  `{"type": "report", "bundle": {...}}` on success,
  `{"type": "invalid", "errors": ["...", ...]}` for validation failures,
  `{"type": "error", "message": "..."}` for RunError/ValueError (user-ready
  sentences, already produced by the engine).
  The client reads it with fetch + ReadableStream (not EventSource).
- `GET /api/sample` → `{available: true, bundle: {...}}` when
  sample_report.json exists and bundle_version == 2, else `{available: false}`.
- `POST /api/export/markdown | /api/export/ideas-csv | /api/export/posts-csv`
  body `{bundle}` → file download (Content-Disposition with
  report_export.filename_stem). Reuses report_export.py verbatim.
- CORS: same-origin (Next.js and functions share the domain); no CORS
  middleware needed, but harmless to allow localhost dev origins.
- Local dev: uvicorn api.index:app on :8000, Next.js dev on :3000 with a
  rewrite of /api/* to :8000 (next.config file, dev-only).

### Frontend (Next.js) requirements

- Single-page product. Order: hero (logo + name + one-line promise) → the
  FORM (query textarea, optional about-you, platform toggle
  LinkedIn/Instagram/Both, window select) → "How it works" (3 steps) BELOW
  the form → sample-report link when available.
- During a run: staged progress UI fed by the real NDJSON progress lines;
  errors render as a dismissible banner above the form; a failed run never
  destroys a rendered report (same principle as the old UI).
- Report sections (same information architecture as the Streamlit app):
  summary chips, Communities analysed (linked r/names, reason, member count,
  muted when used=false), metrics row, Trending topics (horizontal bar chart,
  biggest first, expandable finding + citations), Sentiment (stacked bar +
  counts; plain counts in low_data), Pain points / Questions two-column,
  Content ideas grid (hook is the visual star: quoted, accent border;
  platform + format badges; angle, addresses, rationale, evidence links),
  Activity trend (bar chart), Source data (posts/comments tables, sortable,
  linked), Methodology (collapsible, includes discovery disclosure), footer
  attribution.
- History: last 8 bundles in localStorage, sidebar/drawer list via
  pipeline-style labels, active one marked; exports work for whichever
  bundle is shown.
- Query params `q`, `platform`, `days` read on load and written after a run.
- All Reddit/model-derived text rendered as TEXT (React default escaping;
  never dangerouslySetInnerHTML for it). Citation URLs come from the bundle
  (already archive-verified server-side).
- Design: clean, confident, content-first; one accent colour used with
  restraint; light + dark via prefers-color-scheme; system font stack or a
  self-hosted font (no CDN fetches at runtime); logo per Addendum 1 item 3
  (flat SVG mark: speech bubble + spark) in public/ and used as favicon.
- No em dashes anywhere in UI copy (Addendum 1 item 2 applies to the
  frontend too).
- Sample bundle: fetched from /api/sample (not statically imported), so the
  file on disk stays the single source.

### Vercel specifics

- vercel.json: python function for api/index.py with maxDuration 300 and
  includeFiles for the root *.py modules and sample_report.json; Next.js
  needs no config beyond defaults.
- Env on Vercel (documented in README): GOOGLE_CLOUD_PROJECT,
  GOOGLE_CLOUD_LOCATION, GEMINI_MODEL, GEMINI_TOKEN_BUDGET, plus a service
  account key via GOOGLE_APPLICATION_CREDENTIALS_JSON (README documents
  creating a least-privilege SA with roles/aiplatform.user; api/index.py
  materialises the JSON to a temp file and sets GOOGLE_APPLICATION_CREDENTIALS
  when that variable is present, before any engine import).
- The user deploys; the repo must be deploy-ready (vercel build passes
  locally if the CLI is available, otherwise structure verified against
  current Vercel docs).

## Addendum 3 (2026-07-26): ideas-first report, LinkedIn-only, draft posts, redesign

User feedback on the deployed product. Five changes, bundle becomes v3.

### 1. Platform removed from the product surface (bundle v3)

The product generates LinkedIn-ready ideas, but the word "platform" (and any
LinkedIn/Instagram selector or badge) disappears from the UI and the bundle.
LinkedIn-native writing is an internal property of the prompts.

- `BUNDLE_VERSION = 3`. `inputs = {query, profile, days}` (platform and
  platform_label gone). `content_ideas[*]` loses `platform`; keeps
  {title, hook, format, angle, rationale, addresses, citations}.
- `pipeline.validate_inputs(query, profile, days)`;
  `run_report(query, profile, days, ...)`; PLATFORMS constant deleted.
- `analysis.synthesize(items, topics, query, profile, subreddits,
  preview_chars)`. Prompt targets LinkedIn implicitly: formats menu is
  Text post, Carousel (PDF), Poll, Long-form article, Video script; hooks
  written to stop a professional feed scroll. No platform key in output.
- CLI drops --platform. /api/sample gates on bundle_version == 3.
  sample_report.json regenerated as v3. Frontend types/guards move to v3.
- run_label: "{query truncated} · {n}d".

### 2. New feature: click an idea, get a publish-ready post

- `analysis.draft_post(idea, evidence, query, profile) -> {"post": str}`:
  one Gemini call (thinking on) that writes the complete LinkedIn post for
  that idea: the idea's hook as the opening line (model may polish it),
  short scannable paragraphs with line breaks, concrete substance drawn
  from the supplied evidence excerpts, 120-220 words, 0-3 hashtags at the
  end, no markdown, no links, no em dashes, no "I asked AI" tells.
  Sanitised via strip_markdown applied per line (line breaks preserved).
- API: POST /api/draft, body {idea: {title, hook, format, angle,
  addresses}, evidence: [up to 6 strings], query, profile} -> {"post": str}
  or {"error": msg} with a user-ready sentence. Plain JSON, no streaming.
- Frontend: clicking an idea card opens a dialog: idea title, generated
  post in a publish-look preview (whitespace preserved), Copy button
  (clipboard + copied feedback), Regenerate button, friendly error state.
  Draft starts generating on open; result cached per idea per report view
  so reopening does not re-bill.

### 3. Report is ideas-first

Section order: summary strip (one compact line: query, communities read,
window, item count) -> CONTENT IDEAS (the product) -> a visually quieter
"What the research says" zone: topics, sentiment, pain points + questions,
activity trend -> collapsed-by-default source data and methodology ->
footer. Executive summary becomes a short intro line above the ideas, not
a boxed section.

### 4. Minimal evidence links

No text hyperlinks for citations anywhere (screenshot showed underlined
post titles + date + upvotes dominating the page). Every citation renders
as a small numbered pill/chip (1, 2, 3...) linking to the source, with
title/date/upvotes available on hover (title attribute or tooltip), sized
like a superscript footnote. Applies to idea cards, topics, pain points
and questions. Pain points/questions become tighter list items with their
pills inline after the text.

### 5. Design lift

Idea cards in a responsive grid: 3 columns on desktop, 2 on tablet, 1 on
mobile. Cards are the hero: hook as the headline (quote styling), format
as a small tag, angle as supporting text, "Draft this post" affordance on
hover/click. The rest of the report uses a calmer hierarchy (smaller
headings, muted surfaces, more whitespace, no walls of links). Dark mode
gets elevated card surfaces rather than flat black. Keep the accessibility
and no-CDN rules from Addendum 2.

### 3. Landing layout + design + logo (Addendum 1, superseded where it says Streamlit)

- Landing order: title/caption → FORM (the search box is the product) →
  "How it works" below it → sample button/caption.
- A simple flat SVG logo (assets/logo.svg): speech-bubble-with-spark mark in
  the accent colour, wordmark optional. Wired via `st.logo()` and used as
  page icon.
- Design polish within Streamlit's constraints: `.streamlit/config.toml`
  theme (accent, fonts), refined CSS block in app.py (typography scale,
  card/badge polish, spacing rhythm, hero header). No new dependencies.
