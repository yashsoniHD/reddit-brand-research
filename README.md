# Reddit Brand Research

A Streamlit app that researches any brand's target Reddit community: it fetches recent posts
from a subreddit and uses Groq to surface trending topics, sentiment, pain points, common
questions, and tailored content ideas.

## Features

- Fetches recent posts from any public subreddit (configurable time window: 7/14/30 days)
- Analyses posts with Groq to extract:
  - Trending topics (with links back to source posts)
  - Community sentiment breakdown
  - Top pain points and questions being asked
  - Content ideas tailored to your brand's description
- Works for any brand — just describe it in the form, no code changes needed

## Setup

### 1. Get a Groq API key

Sign up and create an API key at https://console.groq.com/keys

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your key:

```bash
cp .env.example .env
```

Then edit `.env`:

```
GROQ_API_KEY=your-groq-api-key
GROQ_MODEL=llama-3.3-70b-versatile
```

### 3. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Run the app

```bash
streamlit run app.py
```

Then open the app in your browser, fill in your brand name, brand description, and the
subreddit you want to research, and click **Analyse**.
