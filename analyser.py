import json
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

_client = None


def _get_client():
    global _client
    if _client is None:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise ValueError(
                "GOOGLE_CLOUD_PROJECT not set. Add it to your .env file."
            )
        location = os.environ.get("GOOGLE_CLOUD_LOCATION", "global")
        _client = genai.Client(vertexai=True, project=project, location=location)
    return _client


def analyse_posts(
    posts: list[dict], subreddit: str, brand_name: str, brand_description: str
) -> dict:
    if not posts:
        raise ValueError("No posts to analyse.")

    post_snippets = []
    for i, p in enumerate(posts[:150], 1):
        body_preview = (p["body"] or "")[:200].replace("\n", " ").strip()
        post_snippets.append(f"{i}. [{p['date']}] {p['title']}\n   {body_preview}")

    posts_text = "\n\n".join(post_snippets)

    prompt = f"""You are a content strategist for {brand_name}, a brand described as: {brand_description}
You have just read {len(posts)} recent posts from the Reddit community r/{subreddit}.

Here are the posts (title + excerpt):

{posts_text}

---

Analyse these posts and return a JSON object with EXACTLY these keys:

{{
  "trending_topics": [
    {{"topic": "...", "description": "...", "post_count": N, "example": "...", "post_index": N}}
  ],
  "sentiment": {{
    "positive_pct": N,
    "neutral_pct": N,
    "negative_pct": N,
    "summary": "..."
  }},
  "pain_points": ["...", "...", "..."],
  "questions_asked": ["...", "...", "..."],
  "content_ideas": [
    {{"title": "...", "format": "...", "rationale": "..."}}
  ]
}}

Rules:
- trending_topics: top 5 themes by frequency, each with a count estimate, a real example title from the posts, and post_index (the 1-based number of that post from the list above)
- sentiment: percentages must sum to 100; summary is 1-2 sentences
- pain_points: top 5 specific frustrations or struggles mentioned
- questions_asked: top 5 questions the community is asking
- content_ideas: 6-8 ideas tailored for {brand_name} ({brand_description}); format can be "Blog post", "Instagram carousel", "Newsletter", "Short video", "Guide", "Email series", etc.
- Return ONLY valid JSON with no markdown fences, no commentary."""

    client = _get_client()
    model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.3,
            response_mime_type="application/json",
        ),
    )

    return json.loads(response.text)
