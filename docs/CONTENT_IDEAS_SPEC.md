# Content Ideas Pivot — Contract Spec (v2)

> **Historical document.** This spec describes bundle v2. Since then, v3 removed
> the platform input and v4 removed the time-window input (the app always fetches
> the last 30 days) and replaced newest-first sampling with relevance-triaged
> selection (`analysis.rank_items` / `analysis.rate_relevance`). The engine
> quality pass is described in the **v5 addendum at the bottom of this file**,
> which is the current description of the Python layer. Where this document and
> the code disagree, the code, the v5 addendum and the README are current.

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

## Addendum 4 (2026-07-27): engine quality pass (v5 of the Python layer)

An audit of the engine found a set of correctness, cost and output-quality
defects. Everything below is implemented and covered by tests. The bundle stays
at `bundle_version: 4`: the frontend, the API's `/api/sample` gate and the
export layer all key off that number, and nothing here removes or renames a
field they already read. Every change is additive.

### New and changed bundle fields

```
inputs.content_type   "text" | "carousel" | "video" | "article" | null
inputs.discovery      "manual" | "model"     who chose the communities
analysis.report_title short plain title, <= 70 chars, never markdown
stats.engagement.analysed  {"upvotes": int, "comments": int}
stats.sentiment.none_classified  bool
models                every model this run used, sorted
model_label           display names of all of them, joined with " and "
*.citations[*].excerpt  whitespace-normalised evidence, <= 600 chars
posts[*].body / comments[*].body / items[*].body  truncated to ~300 chars
```

`bundle["model"]` still exists and now names the synthesis model (the call whose
words a reader judges), falling back to classification's.

### Inputs

- `validate_inputs(query, profile, days, subreddits, relevance_filter,
  content_type)`. `content_type` is one of `text`, `carousel`, `video`,
  `article`, case-insensitive, `None`/empty meaning a mix. Anything else is a
  friendly error.
- `subreddits` accepts the comma-separated string as before, and now also a JSON
  list of strings (joined with commas). A list containing a bool or a number is
  rejected rather than coerced, as is a bare number or bool.
- The time-range error sentence is generated from `TIME_WINDOWS`, so it cannot
  drift from the allowed set. Non-integral days (7.9) are rejected; `14.0` is not.
- `relevance_filter` accepts real booleans and the unambiguous strings
  (`true/false/1/0/yes/no/on/off`). `bool("false")` is True, which used to switch
  a setting the user turned off back on; anything ambiguous is now an error.

### Cancellation and deadlines

`run_report(..., cancel: threading.Event | None = None, deadline: float | None
= None)`, both keyword-only. `deadline` is an absolute `time.monotonic()` value.

- Checked at every stage boundary: start, after discovery, before each community
  fetch, before classification, before synthesis.
- A set `cancel` event raises `pipeline.RunCancelled`, which carries no
  user-facing message because the only thing that sets it is the client going
  away. The API layer swallows it.
- A passed `deadline` raises `pipeline.RunError` with a sentence the user can act
  on.
- `DEADLINE_RESERVE_SECONDS = 60` is held back for classification and synthesis.
  Once the clock is inside the reserve, no new community is fetched (a notice
  says so) and the run analyses what it already has. The reserved deadline is
  also passed down to `discovery.discover` / `discovery.manual` (stop checking
  candidates) and to `reddit_data.fetch_posts` / `fetch_comments` (stop paging,
  keep the pages already fetched). Injected fakes that predate the parameter are
  detected by signature and called without it.

### Selection and triage

- `TRIAGE_PREVIEW_CHARS` 100 -> 300: 100 characters is a headline and half a
  sentence, which is not enough text to judge relevance on.
- `TRIAGE_BATCH_SIZE` 250 -> 120, and the real batch size is now computed from
  `TRIAGE_BUDGET_SHARE` (previously a constant nobody consulted) so one call
  always fits its share of the budget.
- Triage output reservation is 45 tokens per item, the figure measured for the
  sibling classification call, rather than an unsourced 30.
- `TRIAGE_MAX_POSTS/COMMENTS` stay at 400 each. Covering the whole realistic pool
  (up to 2,000 posts and 1,200 comments) would mean ~27 sequential model calls,
  which no request budget survives. **Instead, untriaged items are their own tier:
  `UNTRIAGED_WEIGHT = 0.35`, below a model-rated 1 (0.55) and above a rated 0
  (backfill).** An item nobody looked at no longer ranks as if a model had called
  it adjacent.
- Relevance ratings are keyed by `relevance_key(kind, item, position)`, a
  `(kind, id-or-#position)` tuple, in both `_select_sample` and `rank_items`. The
  raw id collided across the post and comment namespaces and collapsed every
  id-less item onto one key.
- `_fair_order(*tiers)` shares one prefix cap and one count across the eligible
  and backfill tiers. Ordering each tier separately let a community that filled
  the eligible tier restart at zero and take the head of the backfill tier.
- `reddit_data.fetch_comments` stores `id = comment_id or permalink`, so an
  id-less comment has an identity.

### Model layer

- A response stopped by a content filter (`finish_reason` containing SAFETY,
  PROHIBITED_CONTENT, RECITATION, BLOCKLIST or SPII, or a non-empty
  `prompt_feedback.block_reason`) fails immediately with "Some of the discussion
  in these communities could not be analysed. Try different communities."
  Retrying a filter decision spends a minute reaching the same refusal and then
  reports something permanent as temporary.
- The `_token_limit` module global is gone. A payload rejection shaves a budget
  local to that one run (`classify_sample` -> `fit_sample(budget=...)`) instead of
  permanently shrinking every later run in the process and racing between two
  concurrent ones. `ensure_token_limit` (a no-op stub from the Groq era) is
  deleted, as are `reddit_data.SubredditCheck` and `validate_subreddit`, which
  nothing outside their own tests called.
- `sanitize_classification` rejects a model topic claiming id 0. Zero is the
  prompt's "fits no topic" sentinel, and a topic holding it would collect every
  unassigned item. Items with `t: 0` stay unassigned, as intended.
- `strip_markdown` additionally strips `*emphasis*` / `**bold**`, bare URLs and
  bare domains (TLD list, so "Node.js" and "v3.5" survive). Nothing legitimate in
  a hook, a finding or a topic label contains a link.

### Output quality

- Content ideas: the prompt asks for 12 to 15 (was 8 to 12). The arithmetic is in
  the comment on `MIN_CONTENT_IDEAS`: ~5,000 output tokens against a 24,000
  ceiling. The sanitiser accepts 8 to 20 and warns below 8.
- Hook rules in the synthesis prompt: the "[Problem]? [Imperative]." template is
  banned by name; every hook must carry a number, a phrase quoted from the
  evidence, or a concrete named situation; three good and three bad examples are
  shown. At most 2 of the 15 ideas may name the user's own product or brand.
- `content_type`, when set, forces every idea's `format` to one label
  (`text` -> "Text post", `carousel` -> "Carousel (PDF)", `video` -> "Video
  script", `article` -> "Long-form article") in the prompt and again in the
  sanitiser. Unset keeps the mixed-format menu.
- `report_title`: asked for in the prompt, sanitised to <= 70 chars at a word
  boundary, falling back to the first 60 characters of the query at a word
  boundary.
- Claims: anything under `MIN_CLAIM_CHARS = 25` is dropped ("Pricing" is a
  category, not a finding), and questions are deduped against pain points by a
  case/punctuation-insensitive fingerprint, because the model files the same
  sentence under both headings.
- Draft posts (`analysis.draft_post`): the prompt forbids inventing statistics,
  studies, named tools, companies or personal anecdotes, requires every specific
  to come from the supplied excerpts, and tells the model to write an opinion
  piece rather than invent data when the excerpts have no specifics. The format
  now shapes the output: carousel -> slide-by-slide ("Slide 1:"), video -> beats,
  article -> structured sections, poll -> question plus options, text -> the
  previous behaviour. Word bands follow the format. Evidence excerpts are carried
  at 600 characters, matching the new citation excerpts.

### Citations and numbers

- `stats.citations_for(indices, items, limit, comments=None)` gains `excerpt`
  (the item's body, plus up to two top replies from the same thread for a post,
  falling back to the title, whitespace-normalised, <= 600 chars). The existing
  `text` field is unchanged.
- One thread may only be cited once: a post and two comments from under it are
  one conversation being counted three times (the shipped sample proves it).
  `stats.thread_key` reads the thread id out of the permalink.
- An item with no body and no replies is cited only when nothing better is left.
- `trend_buckets` caps at `max_days + 1`: a 7-day window spans 8 calendar dates,
  and capping at 7 dropped the oldest day of every full window.
- `engagement_totals(posts, items=None)` reports the analysed sample separately.
- `sentiment_breakdown` sets `none_classified` so a renderer can say "no items
  could be classified for tone in this run" instead of drawing an empty chart.

### Robustness

- `reddit_data._safe_int` coerces every numeric field from the archive. The
  archive is a community mirror, not a typed API, and one stringly-typed score
  used to take down a whole fetch.
- A comments fetch that fails no longer discards the posts already fetched for
  that community; a notice says the replies are missing.
- An exception whose message is empty no longer raises IndexError while being
  formatted into a notice.
- An empty community list before fetching raises its own RunError rather than the
  "we could not read anything from (nothing)" one.
- `run_report` takes a `manual=` injection hook alongside `discover=`, so manual
  mode is testable the same way discovery is. Monkeypatching
  `pipeline.discovery.manual` still works.

## Addendum 5 (2026-07-27): content type becomes a multi-select

The advanced "Content type" setting now takes any subset of the five formats
instead of exactly one. Picking two is a normal thing to want (a carousel and a
poll, say), and the single select made it impossible to ask for.

### Request

- `content_types: string[]`, a subset of `text`, `carousel`, `video`, `poll`,
  `article`. Order carries no meaning and duplicates are collapsed. Absent, `[]`
  or all five all mean the same thing: a mix, chosen per idea.
- `content_type: str` is still accepted and reads as a one-item list, because
  shared links and stored history written before this change replay it. The two
  fields add up when both arrive.
- `validate_inputs(query, profile, days, subreddits, relevance_filter,
  content_types, content_type)` returns `content_types` as a sorted list. An
  unrecognised name is a friendly error that quotes the value back
  (`We do not offer a content type called "reel". ...`); a value that is neither
  a list nor a name, and a non-string inside the list, are rejected by shape
  rather than coerced.

### Bundle

- `inputs.content_types` is a sorted list, written only when the run was pinned
  to a real subset. A mixed run carries no such key at all.
- `inputs.content_type` is no longer written. Readers still accept it and render
  it as a list of one, since saved history is full of it.

### Synthesis

- One chosen type behaves exactly as before: every idea comes back in that
  format, stated in the prompt and enforced in `sanitize_synthesis`.
- Two or more: the prompt lists only those labels, tells the model every idea has
  to use one of them, and asks for the ideas to be spread roughly evenly across
  them. `sanitize_synthesis(raw, n_items, topics, query, content_types, profile)`
  coerces any idea whose format is outside the set to the chosen format used
  least so far, ties broken by menu order, which keeps the promise and pulls the
  spread back towards even.
- `analysis.chosen_formats(content_types)` maps wire values to idea-format
  labels and silently drops anything unknown: validation belongs to the input
  layer, and an unrecognised value should leave the ideas alone.
- Draft posts are unchanged. They have always read the format off the individual
  idea, so a mixed set drafts each idea in its own shape.

### Frontend

- A `fieldset` with a `legend` and five labelled checkboxes replaces the select.
  None ticked means any.
- The collapsed advanced-settings summary names a single format and counts
  several ("2 content types").
- The URL parameter `type` is comma-separated (`?type=text,poll`). A legacy
  single value parses without a special case, because splitting one name yields
  that name.
- The methodology row lists the formats the ideas were written for, reading
  `inputs.content_types` and falling back to the legacy `inputs.content_type`.

## Addendum 6 (2026-07-30): channels, and the end of the LinkedIn framing

Reverses Addendum 3 item 1. That change stripped the platform concept from the
product because the app wrote for one professional feed; the framing has since
become the constraint rather than the focus, since the engine (find communities,
search them, cite the evidence, write ideas and drafts) serves any content
creator.

Not a `platform` enum bolted back on. A run carries an optional set of
**channels** that narrows the formats the ideas may take and decides how a draft
is written. A run naming no channel is channel-neutral and behaves exactly as
before, minus the LinkedIn wording, so this is additive for every existing
report, link and history entry.

### No bundle version bump

`BUNDLE_VERSION` stays 5. `Inputs.channels` is optional and the `ContentType`
union only gained values a v5 bundle never carried, which is the precedent
already documented at the top of `lib/types.ts`: additive optional fields do not
bump. Frontend and backend deploy together, so no reader ever meets a key it
cannot label.

### Channels

`analysis.CHANNELS` (wire key -> label) and `analysis.CHANNEL_FORMATS`:

| channel | formats it can ship |
| --- | --- |
| `linkedin` | text, carousel, poll, article, video |
| `instagram` | carousel, short_video, text, poll |
| `tiktok` | short_video |
| `x` | text, thread, poll |
| `youtube` | video, short_video |
| `newsletter` | issue, article |

`analysis.CHANNEL_DRAFT_RULES` carries, per channel, the artefact phrase ("a
TikTok video"), the hashtag ceiling, the poll-option cap (`None` where the
channel has no poll object, in which case the draft prompt never offers one) and
one line of voice instruction. `analysis.draft_rules(channel)` falls back to the
channel-neutral rules (3 hashtags, 4 poll options, no voice line) for an unknown
channel or none, which is what every draft written before channels existed got.

### Formats

`IDEA_FORMATS` grows from five to eight: `short_video` ("Short-form video
script", distinct from the long-form `video`, because a YouTube walkthrough and a
Reel are different artefacts), `thread` ("Thread") and `issue` ("Newsletter
issue", keyed `issue` so a format key cannot be confused with the `newsletter`
channel). `carousel` loses its `(PDF)` suffix, which was the LinkedIn document
post leaking into a label; the channel now decides whether slides are a PDF or
images. `"Carousel (PDF)"` stays in `_FORMAT_ALIASES`, because stored bundles and
replayed links are full of it.

### Precedence: the channel wins

Channels and content types are independent pins that can contradict each other:
a shared link can carry `ch=tiktok&type=poll`, and TikTok has no polls.
`analysis.allowed_formats(channels, content_types)` returns the intersection plus
a flag; an empty intersection falls back to the channel's formats and the flag is
true. `pipeline.run_report` turns that flag into a notice naming both sides.
Refusing the run would break old links and honouring the format would hand back
something unpostable, so the channel wins and the report says so. This follows
the doctrine `chosen_formats` already stated: unknown values are dropped rather
than refused.

### Surfaces

- `validate_inputs(..., channels=...)` and `run_report(..., channels=...)`;
  `selected_channels` mirrors `selected_content_types` (every channel is the same
  request as none, so it comes back empty). `inputs["channels"]` is written only
  when the run was pinned to a real subset.
- `synthesize(..., channels=...)` reaches `_synthesis_prompt`, which names the
  channels and builds the format rule from `allowed_formats`. The persona is "a
  content strategist for a creator growing an audience"; the hook rule says
  "stop a scroll on {channels}" or "stop a feed scroll". `channels` goes through
  the `_accepts` signature probe at the call site, like `deadline`, so an
  injected fake that predates it still runs.
- `draft_post(..., channel=...)` and `POST /api/draft` with an optional
  `channel`. An unknown one is dropped, not refused: the post is still writable
  channel-neutral and a stale client should not lose a draft over it.
- `scripts/run_report.py --channels`; a "Written for" line and a Channels row in
  the Markdown export.
- CLI, URL (`?ch=linkedin,instagram`) and methodology row all round-trip.

### Frontend

- A Channels fieldset sits directly above Content types, because one filters the
  other: `formatsForChannels` shrinks the format menu to what the ticked channels
  can ship, and a format tick that falls outside the new set is dropped in the
  same update. A tick the run would not honour is worse than one that disappears.
- The draft dialog names the channel ("Your Instagram draft") and, for a run with
  more than one, offers a switcher. The channel is part of the dialog body's key,
  so switching remounts into a fresh request the way reopening does. The in-page
  draft cache is keyed by `index|channel`.
- **Known limitation:** `db.py` keys saved drafts on `(report_id, idea_index)`
  alone, so drafting one idea for two channels overwrites the first. Extending
  the key is a Supabase migration and was left out of scope; last write wins.

### Settings panel redesign (same change, `components/query-form.tsx`)

The panel is nested inside a pure-white `surface-raised` card, where the page's
three-plane stack has already bottomed out: warm `--hush` (#f1f1ec) read as a
smudge on white, and the `open:bg-card` fix that replaced it was white on white
held by a 10%-opacity hairline. So the panel is now **unfilled**, separated by a
top rule and a left `--brand-line` rule with indentation, which is what
`globals.css` says the elevation system is for. No new token; `.surface-hush`
stays for the report zone.

Channels, content types and the time range now share one chip idiom (`CHIP_BASE`
/ `CHIP_ON` / `CHIP_OFF`) instead of two. The chip border uses `--input`, not
`--border`: with no plate behind them, an unticked borderless chip is
indistinguishable from a row of plain labels. Channels and content types stay
real `<input type="checkbox">` elements kept `sr-only` inside their labels
(multi-select), styled through `has-focus-visible:`; only the time range is
single-select and keeps its `role="radio"` markup. Field order is About you ->
Channels -> Content types -> Communities -> Time range -> Relevance filter.
