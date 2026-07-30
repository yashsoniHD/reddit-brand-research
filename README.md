# Reddit Content Ideas

Type what you're looking for. The app finds the Reddit communities where people
actually discuss it, reads what they say, and turns it into content ideas, each with
a scroll-stopping hook and the evidence behind it.

Name the channels you publish on (LinkedIn, Instagram, TikTok, X, YouTube, your
newsletter) and the ideas come back in the formats those channels can actually
ship, written in the register each one rewards. Name none and the ideas are not
tied to one place.

Built for anyone who needs to show their work: every claim in the report links to the
Reddit post or comment behind it, and every number is computed from the data rather
than estimated by a language model.

## How it works

1. **Describe what you're looking for.** One text box, plus an optional line about
   who you are or your brand (tailors the ideas to your voice and audience). That's
   the whole form.
2. **We find and search the communities.** A model proposes candidate subreddits for
   your query, along with the words worth searching each one for; every name is
   checked against the Reddit archive to confirm it exists and is public before it's
   read. The model proposes, the archive verifies. Each community is then searched
   for those words, so what gets analysed is the threads that match your brief
   rather than everything the community posted. A search is not time-bounded: it
   takes the most recent matches however far back they reach. Naming the communities
   yourself switches to reading them in full over the last 7 days instead. From
   what comes back, the strongest candidates are rated for relevance to your
   query by the model, then ranked in code by relevance, community engagement and
   recency to pick the sample that gets analysed.
3. **Get feed-ready ideas with evidence.** 8 to 12 content ideas, each with a hook
   (the literal opening line), a native format, an angle, and citations back to the
   real posts and comments that inspired it, backed by trending topics, pain points
   and questions pulled from the same data.

## What it produces

- **Executive summary.** The single most important takeaway for your content strategy.
- **Communities analysed.** Which subreddits were chosen, why, their subscriber
  count, and whether they ended up contributing data.
- **Trending topics.** Share of the conversation per topic, each with a finding and
  up to three real, linked source items.
- **Sentiment.** Positive / neutral / negative, classified per item and counted in
  Python.
- **Pain points and questions.** In the community's own words, each with citations.
- **Content ideas.** 8 to 12 platform-native ideas (Text post, Carousel, Reel script,
  Poll, Video script, Story series, and more), each with a hook, a format, an angle,
  and the evidence behind it.
- **Activity trend** and a sortable **source data** table for posts and comments.
- **Methodology panel.** Sample sizes, real date coverage, exclusions, data lag, model.
- **Exports.** The full report as Markdown, plus source posts and content ideas as CSV.

## Screenshots

<!-- Add screenshots here:
     docs/screenshots/landing.png     empty state with "How it works"
     docs/screenshots/report-top.png  executive summary, metrics, communities analysed
     docs/screenshots/ideas.png       content idea cards with hooks
-->

_Screenshots to be added._ Until then, click **See a sample report** in the running
app once `sample_report.json` has been regenerated against the new pipeline.

## Setup

The analysis runs on Gemini through Vertex AI. There is no API key: the app
authenticates with Application Default Credentials, so whoever is signed in with
`gcloud` on the machine is who calls the model.

### 1. Sign in to Google Cloud

```bash
gcloud auth application-default login
```

### 2. Point the app at a project with Vertex AI enabled

```bash
gcloud services enable aiplatform.googleapis.com --project YOUR_PROJECT_ID
cp .env.example .env
```

```
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
```

`GOOGLE_CLOUD_LOCATION` is optional and defaults to `global`, which routes to
whichever region has capacity. `GEMINI_MODEL` is optional too and defaults to
`gemini-2.5-flash`, with `gemini-2.5-flash-lite` as the automatic fallback.

If either step is missing, the app says which one in plain language rather than
failing with a stack trace.

### 3. Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt        # add -r requirements-dev.txt to run the tests
```

The pinned versions in `requirements.txt` are the ones this was built and tested against;
`.python-version` records the interpreter version.

### 4. Run

The product is a Next.js frontend backed by a FastAPI layer over the Python engine
(`api/index.py`). Run both in development:

```bash
# Terminal 1: the API, on :8000
uvicorn api.index:app --reload --port 8000

# Terminal 2: the Next.js app, on :3000
npm install
npm run dev
```

Next.js proxies `/api/*` to `http://localhost:8000` in development (see the dev-only
rewrite in `next.config.ts`), so open `http://localhost:3000` and everything, including
the API calls, works from that one origin. The API alone can also be exercised directly:

```bash
curl http://localhost:8000/api/sample
curl -N -X POST http://localhost:8000/api/report \
  -H 'Content-Type: application/json' \
  -d '{"query": "content ideas about time management for busy founders", "profile": ""}'
```

`POST /api/report` streams newline-delimited JSON: one `{"type": "progress", ...}` line
per pipeline step, then exactly one terminal line, either `{"type": "report", "bundle": {...}}`,
`{"type": "invalid", "errors": [...]}` for bad input, or `{"type": "error", "message": "..."}`
for a run that failed. Read it with `fetch` and a `ReadableStream`, not `EventSource`.

## Deploy to Vercel

The repo deploys as a single Vercel project: Next.js builds normally, and
`api/index.py` becomes one Python Vercel Function that `vercel.json` routes all
`/api/*` requests to (`rewrites`), reusing every engine module and `report_export.py`
verbatim. `vercel.json` also sets `maxDuration: 300` on that function (a live run
with real Vertex and Arctic Shift calls can take a few minutes) and `includeFiles`
so the root `*.py` modules and `sample_report.json` ship in the function bundle.

### Environment variables

Set these in the Vercel project's environment variables (Production and Preview):

- `GOOGLE_CLOUD_PROJECT`: the GCP project with Vertex AI enabled.
- `GOOGLE_CLOUD_LOCATION`: optional, defaults to `global`.
- `GEMINI_MODEL`: optional, defaults to `gemini-2.5-flash`.
- `GEMINI_TOKEN_BUDGET`: optional, defaults to `200000`.
- `GOOGLE_APPLICATION_CREDENTIALS_JSON`: the full JSON key of a service account,
  as a single-line string (see below). There is no `gcloud` session on Vercel,
  so this replaces the Application Default Credentials used in local dev.

### Creating the service account

Vercel Functions cannot use your local `gcloud auth application-default login`
session, so create a dedicated, least-privilege service account for deployment:

```bash
gcloud iam service-accounts create reddit-content-ideas \
  --project YOUR_PROJECT_ID \
  --display-name "Reddit Content Ideas (Vercel)"

gcloud projects add-iam-policy-binding YOUR_PROJECT_ID \
  --member "serviceAccount:reddit-content-ideas@YOUR_PROJECT_ID.iam.gserviceaccount.com" \
  --role "roles/aiplatform.user"

gcloud iam service-accounts keys create service-account.json \
  --iam-account "reddit-content-ideas@YOUR_PROJECT_ID.iam.gserviceaccount.com"
```

Paste the contents of `service-account.json` into the `GOOGLE_APPLICATION_CREDENTIALS_JSON`
environment variable in the Vercel dashboard, then delete the local file; it is a
credential and should not be committed or kept lying around. At import time,
`api/index.py` writes that JSON to a temporary file and points
`GOOGLE_APPLICATION_CREDENTIALS` at it, before any engine module is imported, so
the rest of the code never has to know the difference between ADC and a key file.

## Running a report from the terminal

The same pipeline the UI uses, from the terminal instead of the browser. Useful for
smoke tests, for scripting, and for regenerating the bundled sample:

```bash
python scripts/run_report.py \
  --query "Productivity struggles freelance designers have juggling multiple clients" \
  --profile "I run a Notion template shop for freelance designers" \
  --json sample_report.json --markdown report.md --sample
```

Pass `--subreddits` to skip discovery entirely and analyse specific communities
instead, which is useful for deterministic regeneration and for debugging one
community in isolation:

```bash
python scripts/run_report.py \
  --query "Productivity struggles freelance designers have juggling multiple clients" \
  --subreddits productivity,freelance
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

The suite is offline: HTTP and model calls are faked, so it runs in well under a second.
It covers subreddit input normalisation and lookup, community discovery (candidate
sanitisation, archive vetting, fail-open behaviour), the sanitisation of malformed model
output (including hook and platform coercion), every deterministic statistic,
orchestration and partial-failure behaviour, the exporters, and the shape of the shipped
sample report (once regenerated for the current bundle version).

## Architecture

```
app/, components/, lib/   Next.js frontend. Rendering and state only, no business logic
api/index.py       FastAPI layer exposing the pipeline as a Vercel Function
pipeline.py        Orchestration: input -> discovery -> data -> labels -> numbers -> one report bundle
discovery.py       Turns a free-text query into vetted candidate subreddits
reddit_data.py     Data layer. The only module that knows where Reddit data comes from
analysis.py        The Gemini calls on Vertex AI (discovery, classification, synthesis), prompt construction, and output sanitisation
stats.py           Every number in the report. Pure Python, no network, no model
report_export.py   Markdown report and CSV builders
scripts/run_report.py   Terminal entry point to the same pipeline
sample_report.json A real report bundle, rendered through the normal code path
```

Three ideas hold the design together:

**The data source is swappable.** `reddit_data.py` is the only file that imports `requests`
or knows an endpoint URL. It exposes normalise, parse, validate, lookup, fetch-posts,
fetch-comments, search-posts and fetch-comments-for-posts functions, and everything above
it consumes plain dictionaries. Swapping the Arctic Shift archive for the official Reddit
API, Pushshift, or a database means reimplementing that one module. What the archive will
and will not do, including the constraints that shaped the search path, is recorded in
[docs/ARCTIC_SHIFT_API.md](docs/ARCTIC_SHIFT_API.md).

**The model proposes; the archive verifies.** Discovery is a single model call asking
"where on Reddit do people discuss this", because a language model is good at that
question and bad at knowing whether the community it just named actually exists. Models
invent plausible-looking subreddits constantly. Every candidate name is normalised, deduped
and checked against the archive before it can reach the fetch stage, and discovery can
still miss a niche or newly-created community the model doesn't know about.

**The model labels; Python counts.** The first analysis call only classifies items, by
topic and by sentiment, and the second only writes prose about those labels. Topic
volumes, percentages, engagement totals, date ranges and every citation are computed in
`stats.py` from our own fetched data. A model cannot inflate a percentage or invent a
source link, because it is never asked for either.

Everything the UI shows, the CLI prints and the exports contain comes from one report
bundle, so those three views cannot drift apart. The frontend keeps report bundles in
component state, backed by `localStorage` for the history drawer, and a failed run never
clears the report already on screen; it surfaces an error alongside it instead.

## Known limitations

- **Discovery can miss niche communities.** The model proposes subreddits from its own
  knowledge of Reddit, so a small, new, or unusually named community can be missed even
  when it is exactly on-topic. The report lists every candidate the model considered, not
  only the ones that were used, so you can see what the search actually covered.
- **The model proposes; the archive verifies.** Every discovered subreddit is checked
  against the Reddit archive to confirm it exists and is public before it's read. If
  the lookup service itself is unavailable for every candidate, discovery trusts the
  model's names and lets the fetch stage sort out anything that turns out not to exist.
- **Arctic Shift dependency.** Reddit data comes from the public Arctic Shift archive. If
  that service is down or slow, reports cannot be generated; the app retries transient
  failures and keeps partial pages rather than discarding a good fetch.
- **Data lag.** The archive trails live Reddit, typically by an hour or two. Every report
  states the timestamp of the newest item it saw and how far behind that was.
- **Sample size is capped on purpose.** A run analyses at most 150 posts and 150
  comments, chosen from everything retrieved. Selection is hybrid:
  the model rates the strongest candidates for relevance to your query (a cheap
  triage call, and the only thing the model decides), then code ranks them by
  relevance, engagement normalised within each community, and recency, with no
  single community taking more than half the sample while others still have items
  to offer. If the triage call fails, selection falls back to engagement and recency
  alone rather than failing the run, and the methodology panel says which happened.
  Gemini's context window is far larger than the sample, so the cap is about cost and
  speed rather than a provider ceiling: `GEMINI_TOKEN_BUDGET` (default 200000) is the
  token budget a single request may spend, and the sample ladder picks the largest
  shape that fits it. If Vertex ever rejects a request as oversized, the ladder shaves
  the budget and refits rather than failing the run. The report always states the real
  numbers ("Posts analysed: 40 of 300 fetched") and says so when the budget, not the
  available data, is what limited the sample. Vertex quotas are per minute, not per
  day, so a rate limit is a short wait rather than a lockout, and the app retries
  before reporting one.
- **Topic findings are bound to topics by label, not by id.** Asked for `{id, finding}`,
  models mis-key the pairing often enough to matter, which silently attaches a finding to
  the wrong topic. The prompt requires the label to be echoed back and the label decides
  where the finding lands.
- **Sentiment is model-judged.** The percentages are counted deterministically, but the
  per-item labels behind them are a model's reading of tone. They are directional, and the
  report says so wherever they appear.
- **Under ten items, results are directional.** Quiet communities are flagged, and raw
  counts replace percentages so a 1-of-3 sample never renders as "33%".
- **Markdown injection is mitigated, not eliminated.** Reddit bodies can contain markdown
  images and links, and a model asked to summarise them will echo them. All model text,
  including hooks, reasons, findings and every other generated field, is stripped of
  image and link syntax before it reaches the UI, the report or the CSVs, so model output
  cannot introduce remote content or a clickable link. Post titles and bodies shown in
  tables and citations are rendered as data, not as markdown. The same sanitiser strips em
  and en dashes out of generated text, because the prompts ban them and a prompt rule is a
  request rather than a guarantee.
- **Local, per-browser history.** Reports are kept in that browser's `localStorage`, capped
  at the 8 most recent (sample reports are never remembered). History does not sync across
  devices or browsers, and private-mode or blocked-storage browsers do not persist it at
  all; use the export buttons to keep a copy that survives all of that.

## Data and privacy

Only public Reddit posts and comments are read. There is no Reddit login and no posting.
Your query and profile description are sent to Gemini on Vertex AI, in your own Google
Cloud project, as part of the discovery and analysis prompts, and are not stored anywhere
by this app.
