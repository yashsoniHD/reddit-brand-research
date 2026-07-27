# Reddit Brand Research — v1 Sellable Rebuild Spec

> **Historical document.** This spec predates the content-ideas pivot, the move
> from Groq to Gemini on Vertex AI, the removal of Streamlit, the removal of the
> platform and time-window inputs, and the relevance-triaged sample selection.
> Where this document and the code disagree, the code and README are current.

Goal: transform the current 3-file demo into a polished, trustworthy, exportable Reddit
research tool for content/brand teams and agencies. Keep the name "Reddit Brand Research".
Keep Groq as the LLM provider. Data source stays Arctic Shift, but isolated behind a
swappable module.

## Architecture (new file layout)

```
app.py                  — Streamlit UI only (rendering, state, no business logic)
reddit_data.py          — data layer: fetch posts, fetch comments, validate subreddit,
                          normalize subreddit input. The ONLY file that knows about
                          Arctic Shift. Swappable by design.
analysis.py             — LLM pipeline: classification call + synthesis call, output
                          sanitization/validation, Groq client with retry + model fallback
stats.py                — pure-Python deterministic computation: sentiment aggregates,
                          topic volumes, trend buckets, brand mentions, engagement totals,
                          actual date ranges. NO LLM, NO network. Fully unit-testable.
report_export.py        — Markdown report builder + CSV builders (source data, content ideas)
.streamlit/config.toml  — minimal toolbar, showErrorDetails=false, light theme polish
sample_report.json      — pre-generated sample analysis bundle for the "See a sample" button
tests/                  — pytest unit tests for stats.py, normalization, sanitization
```

## Data layer (reddit_data.py)

- Keep existing hardened pagination/retry logic for posts (max 5 pages × 100).
- `normalize_subreddit(raw) -> str`: strip whitespace; case-insensitively strip
  `r/`, `/r/`, and full URLs (`https://www.reddit.com/r/x/...` → `x`) via regex
  `^(https?://)?(www\.|old\.)?(reddit\.com)?/?(r/)?` (case-insensitive), strip
  trailing `/` and whitespace; raise ValueError with helpful message if result
  contains spaces or invalid chars (valid: letters, digits, underscore, hyphen, 3-21 chars).
- `parse_subreddit_input(raw) -> list[str]`: split on commas, normalize each, dedupe,
  cap at 3 (error message if more).
- `validate_subreddit(name) -> bool | suggestions`: GET
  `https://arctic-shift.photon-reddit.com/api/subreddits/search?subreddit=<name>&limit=5`
  (prefix match). Exact case-insensitive match in results → valid (return canonical
  casing). No exact match → invalid, return up to 3 prefix suggestions for the error
  message. On endpoint failure, fail OPEN (skip validation, proceed to fetch).
- `fetch_posts(subreddit, days)`: as today PLUS: skip posts whose selftext is
  `[removed]`/`[deleted]` AND title is empty; dedupe by post id/permalink across pages;
  capture `created_utc` (raw int) alongside formatted date for trend bucketing.
- `fetch_comments(subreddit, days, limit=300)`: GET `/api/comments/search` with
  `subreddit`, `after` (same cutoff), `limit=100`, paginate up to 3 pages with the same
  retry/partial-results pattern. Fields: body, score, permalink URL
  (`https://reddit.com{permalink}`), created_utc, date. Skip `[removed]`/`[deleted]`
  bodies. Return newest-first.
- Multi-subreddit: caller loops; each post/comment dict carries a `subreddit` field.
- Module-level named constants for all magic numbers (page size, max pages, timeouts,
  retry counts).

## Analysis pipeline (analysis.py) — LLM classifies, Python computes

Two Groq calls per run (not one):

**Call 1 — classification.** Input: numbered compact list of items (posts first, then
comments; posts: title + 200-char body preview; comments: 200-char body preview). Cap:
150 posts + 150 comments (by recency for posts, by score for comments). Output JSON:
```json
{
  "topics": [{"id": 1, "label": "...", "item_indices": [3, 17, ...]}],   // 4-6 topics
  "item_sentiments": [{"i": 1, "s": "pos|neu|neg"}, ...]                  // EVERY item
}
```
Topic label rules in prompt: must be specific findings, never generic category headers,
never contain the subreddit's own name or the words "discussion/general".

**Call 2 — synthesis.** Input: the classified items + topic labels + brand name/description.
Output JSON:
```json
{
  "executive_summary": "2-3 sentence single most important takeaway for {brand}",
  "topic_descriptions": [{"id": 1, "finding": "1-2 sentence FINDING, not label restatement"}],
  "pain_points":     [{"text": "...", "item_indices": [..]}],   // top 5
  "questions":       [{"text": "...", "item_indices": [..]}],   // top 5
  "content_ideas":   [{"title": "...", "format": "...", "angle": "...",
                       "rationale": "...", "addresses": "pain point or topic text",
                       "item_indices": [..]}]                    // 6-8
}
```

**Sanitization (`sanitize_*` functions):** after each call, coerce every field to a safe
shape — missing/null keys → defaults, every `item_indices` filtered to valid range ints
(`isinstance(i, int) and 1 <= i <= n_items`), strings coerced via `str()`, lists via
`or []`. Drop topics/pain points whose indices ALL fail validation. Strip markdown
image/link syntax from all LLM strings before they reach the UI
(`re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', s)`).

**Groq client:** retry 429/5xx with backoff (3 attempts, 1s/2s) mirroring the fetcher
pattern; explicit `max_tokens` (8000 call 1, 4000 call 2); `temperature=0`;
`response_format json_object`; on `model_decommissioned`/400-model errors retry once with
fallback model `llama-3.1-8b-instant`; map RateLimitError/AuthenticationError/APIError to
clean ValueError messages ("Analysis service is busy — try again in a minute", "Analysis
service API key is invalid or missing", etc.). Catch json.JSONDecodeError and empty
choices → ValueError("The analysis came back incomplete — please retry."). Read key from
env, with `st.secrets` fallback guarded by try/except. Basic `logging` calls (run start,
counts, latency, errors) to stdout.

## Deterministic stats (stats.py) — pure Python

- `sentiment_breakdown(item_sentiments, n_items)` → counts + percentages (ints summing
  to 100 via largest-remainder rounding), computed ONLY from classified items.
- `topic_volumes(topics, n_items)` → per-topic count + share %, sorted desc.
- `trend_buckets(posts, days)` → posts-per-day counts (list of (date, count)) for the
  window, suitable for a bar chart.
- `brand_mentions(items, brand_name)` → case-insensitive whole-word-ish search of brand
  name in title+body; returns matching items with their sentiment labels + counts.
  (Word-boundary regex; skip if brand name < 3 chars.)
- `engagement_totals(posts)` → total score, total comments.
- `actual_date_range(posts)` → (oldest, newest) formatted dates + newest-post timestamp
  for the recency trust line.
- `citations_for(indices, items)` → list of {title_or_excerpt, url, date, score, type}
  resolved from OUR data (never LLM text). Cap 3 per claim.

## UI (app.py)

**Page setup:** keep `layout="wide"` but wrap all prose in a centered ~900px column
(`st.columns([1, 8, 1])` pattern or container widths); full width only for tables/charts.
`.streamlit/config.toml`: `[client] toolbarMode="minimal"`, `showErrorDetails=false`;
a light custom theme (primaryColor a professional blue/orange, clean font).

**Landing (empty state):**
- Title + outcome-focused caption: "See what your customers are actually talking about
  on Reddit — and turn it into a content plan. Built for content and brand teams."
- A 3-step "How it works" strip (3 columns): 1. Describe your brand → 2. Pick up to 3
  communities → 3. Get an evidence-cited research report in ~60 seconds.
- "See a sample report" button → loads bundled `sample_report.json` instantly and renders
  the full results UI from it (clearly bannered as a sample).
- Trust caption: "Analyses public Reddit posts and comments via the Arctic Shift archive ·
  No Reddit login needed · Your brand details are never stored."

**Form:**
- Brand name (max_chars=80), Brand description as text_area (max_chars=500) with VISIBLE
  helper text under label ("What the brand does and who it serves — this drives the
  content ideas. Not sure? Paste your homepage tagline."), Subreddits input (placeholder
  "e.g. skincareaddiction — up to 3, comma-separated; names or full Reddit URLs work"),
  time window select (7/14/30).
- On submit: validate ALL fields at once; show all errors together as st.error list;
  keep it inside/adjacent to the form.
- Submit button disabled while a run is in flight (use session_state flag).

**Run experience:** `st.status` with real staged updates: "Checking r/{x} exists…" →
"Fetching posts from r/{x}… ({n} so far)" → "Fetching comments…" → "Classifying {n}
posts and {m} comments…" → "Writing the report for {brand}…" → collapse on success with
elapsed time. Expectation line before: "Usually takes 30–60 seconds."

**State model (critical):**
- Results stored in `st.session_state.report` (a single dict bundle: inputs, posts,
  comments, analysis, stats, generated_at). Rendering happens OUTSIDE the submit branch,
  from session_state — a rerun/refresh must never blank a report.
- A failed run must NOT clobber an existing report: run into a temp var, only assign on
  success; on failure show the error banner ABOVE the still-visible previous report
  ("Couldn't run r/{new} — showing your previous report.").
- Query params: on success set `?brand=&subs=&days=`; on cold load with params present,
  prefill the form.
- Run history: sidebar list of this session's runs (brand × subs × window × time); click
  → re-render that report from session_state instantly. Sidebar also holds the export
  buttons for the active report.
- `@st.cache_data(ttl=3600)` on fetch_posts/fetch_comments (keyed by subreddit+days);
  cache the two LLM calls with `@st.cache_data(ttl=3600)` keyed on input hash too, so
  identical reruns are free.

**Results layout (in order):**
1. Compact run summary chip row + "New analysis" affordance (form collapses into an
   expander after success, expanded=False).
2. **Executive summary** — st.info or styled container, the quotable takeaway.
3. Honest metrics row: "Posts analysed: 150 of 432 fetched" (or plain count when under
   cap), "Comments analysed: N", "Actual range: 19–26 Jul 2026", "Total engagement:
   12,4k upvotes · 3,1k comments". Caption when truncated: "Analysis covers the 150 most
   recent posts and top 150 comments by score."
4. **Trending topics**: horizontal bar chart of topic volumes (share of conversation),
   then top-2 topics expanded / rest collapsed; each topic: finding description +
   up to 3 REAL citations (title, linked, date) resolved from item_indices.
5. **Sentiment**: single stacked horizontal bar (green/grey/red) built from deterministic
   percentages + summary caption: "Classified per-item by the model across {n} posts and
   {m} comments; percentages computed from those classifications."
6. **Brand mentions** (only if any): "{brand} was mentioned in N posts/comments (X pos /
   Y neu / Z neg)" + linked list (cap 10). If zero: one-line "No direct mentions of
   {brand} found in this sample." — still valuable info.
7. **Pain points & questions** two-column: each item with 1-3 linked citations.
8. **Content ideas**: 2-col grid of bordered containers; format as `st.badge`-style pill
   (not code backticks); each card shows title, angle, "Addresses: {pain point}",
   rationale; st.code-free. (st.badge exists in recent Streamlit; if unavailable, use a
   small colored markdown span via container + caption.)
9. **Activity trend**: bar chart posts/day across the window (from trend_buckets).
10. **Source data**: st.dataframe with columns Subreddit / Title / Date / Score /
    Comments / Link (LinkColumn), sortable, ~500px height — replaces the giant expander.
    A second tab or toggle for comments table.
11. **Methodology panel** (expander, always present): sample sizes, date range,
    subreddits, model name, newest-post timestamp ("data ~2h behind live Reddit"),
    exclusions ([removed]/[deleted] skipped, truncation rules), "numbers are computed
    from per-item classifications, not model estimates", generated-at timestamp.
12. Footer: "Source: public Reddit data via Arctic Shift archive · Report generated
    {timestamp} · Analysis: Llama 3.3 70B on Groq".

**Low-data floor:** if total items < 10 → st.warning before results: "Only {n} items
found — results are directional, not conclusive. Try a 30-day window." and skip
percentages (show raw counts instead).

**Error UX:** all user-facing errors are complete, friendly sentences; nonexistent
subreddit error offers suggestions from validate_subreddit; never show raw exception
text, status codes, or URLs. Multi-subreddit partial failure: if ≥1 subreddit succeeds,
proceed with a notice chip "r/{bad} skipped: not found".

## Exports (report_export.py)

- `report_markdown(bundle) -> str`: full report as clean Markdown (all sections incl.
  methodology + citation links).
- `posts_csv(bundle) -> str`, `ideas_csv(bundle) -> str` via csv module.
- Sidebar download buttons: "Report (.md)", "Source posts (.csv)", "Content ideas (.csv)"
  with filenames like `reddit-research-{brand}-{date}.md`.

## Housekeeping

- requirements.txt: pin all versions (`streamlit==`, `groq==`, `requests==`,
  `python-dotenv==`, `pandas==` for dataframe, `pytest==` in a requirements-dev.txt).
- `.python-version` file with the venv's version.
- README rewrite: features, screenshots section placeholder, setup, architecture note
  (data layer swappable), known limitations (Arctic Shift dependency, ~2h lag,
  markdown-injection mitigation).
- Sample report: generate once during the build by running the real pipeline
  (brand "Notion", description "Notion is an all-in-one workspace for notes, docs, and
  project management", subreddit "productivity", 7 days) and saving the full bundle to
  sample_report.json (strip nothing; it renders through the same code path).
- Unit tests (pytest): normalize_subreddit/parse_subreddit_input cases (URLs, R/, spaces,
  commas, >3), sanitize functions against malformed LLM output (missing keys, nulls,
  string indices, out-of-range indices, markdown injection strings), stats functions
  (rounding to 100, brand mentions word-boundary, trend buckets, citations resolution).
- Keep `.env` untouched; never commit it.

## Definition of done

1. `pytest` passes.
2. `py_compile` on all modules passes.
3. A real CLI end-to-end run (fetch r/productivity posts+comments → classify → synthesize
   → stats → report_markdown) completes and the markdown contains: honest sample-size
   line, ≥1 real permalink per topic/pain point, sentiment summing to 100.
4. Sample report json generated and loads.
5. Streamlit boots clean (`streamlit run app.py` starts without exceptions).
6. No file contains "Yellow", "menopause", "Claude AI", or raw API URLs in user-facing strings.
