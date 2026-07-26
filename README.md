# Reddit Brand Research

A Streamlit app that researches any brand's target Reddit community: it fetches recent posts
from a subreddit and uses Gemini (via Vertex AI) to surface trending topics, sentiment, pain
points, common questions, and tailored content ideas.

## Features

- Fetches recent posts from any public subreddit (configurable time window: 7/14/30 days)
- Analyses posts with Gemini on Vertex AI to extract:
  - Trending topics (with links back to source posts)
  - Community sentiment breakdown
  - Top pain points and questions being asked
  - Content ideas tailored to your brand's description
- Works for any brand — just describe it in the form, no code changes needed

## Setup

### 1. Authenticate with Google Cloud

```bash
gcloud auth application-default login
```

### 2. Enable the Vertex AI API on your project

```bash
gcloud services enable aiplatform.googleapis.com --project=YOUR_PROJECT_ID
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in your project details:

```bash
cp .env.example .env
```

Then edit `.env`:

```
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
GOOGLE_CLOUD_LOCATION=global
GEMINI_MODEL=gemini-2.5-flash
```

### 4. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 5. Run the app

```bash
streamlit run app.py
```

Then open the app in your browser, fill in your brand name, brand description, and the
subreddit you want to research, and click **Analyse**.
