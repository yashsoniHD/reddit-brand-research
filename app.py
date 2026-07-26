import streamlit as st
from reddit_fetcher import fetch_posts
from analyser import analyse_posts

st.set_page_config(
    page_title="Reddit Brand Research",
    page_icon="🔍",
    layout="wide",
)

st.title("🔍 Reddit Brand Research")
st.caption("Understand what any community is talking about and generate content ideas for your brand.")

with st.form("analyse_form"):
    brand_name = st.text_input(
        "Brand name",
        placeholder="e.g. Acme Skincare",
    )
    brand_description = st.text_area(
        "Brand description",
        placeholder="e.g. Acme Skincare makes fragrance-free, dermatologist-tested skincare for people with sensitive, eczema-prone skin.",
        help="What the brand does, its focus area, and who it serves.",
    )
    col1, col2 = st.columns([3, 1])
    with col1:
        subreddit = st.text_input(
            "Subreddit name",
            placeholder="e.g. SkincareAddiction, eczema",
        )
    with col2:
        days = st.selectbox("Time window", [7, 14, 30], index=0, format_func=lambda d: f"Last {d} days")
    submitted = st.form_submit_button("Analyse", type="primary", use_container_width=True)

if submitted:
    if not brand_name.strip():
        st.warning("Please enter a brand name.")
        st.stop()

    if not brand_description.strip():
        st.warning("Please enter a brand description.")
        st.stop()

    if not subreddit.strip():
        st.warning("Please enter a subreddit name.")
        st.stop()

    brand_name = brand_name.strip()
    brand_description = brand_description.strip()
    subreddit = subreddit.strip().removeprefix("r/")

    with st.spinner(f"Fetching posts from r/{subreddit}..."):
        try:
            posts = fetch_posts(subreddit, days=days)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Could not reach Reddit: {e}")
            st.stop()

    if not posts:
        st.warning(f"No posts found in r/{subreddit} from the last {days} days.")
        st.stop()

    with st.spinner(f"Analysing {len(posts)} posts with Gemini..."):
        try:
            analysis = analyse_posts(posts, subreddit, brand_name, brand_description)
        except ValueError as e:
            st.error(str(e))
            st.stop()
        except Exception as e:
            st.error(f"Analysis failed: {e}")
            st.stop()

    # ── Summary stats ──────────────────────────────────────────────
    st.divider()
    c1, c2, c3 = st.columns(3)
    c1.metric("Posts analysed", len(posts))
    c2.metric("Date range", f"Last {days} days")
    c3.metric("Subreddit", f"r/{subreddit}")

    # ── Trending Topics ────────────────────────────────────────────
    st.subheader("📈 Trending Topics")
    topics = analysis.get("trending_topics", [])
    for t in topics:
        with st.expander(f"**{t['topic']}** — ~{t.get('post_count', '?')} posts"):
            st.write(t.get("description", ""))
            if t.get("example"):
                st.caption(f"Example: *\"{t['example']}\"*")
            idx = t.get("post_index")
            if idx and 1 <= idx <= len(posts):
                st.markdown(f"[View source post ↗]({posts[idx - 1]['url']})")

    # ── Sentiment ─────────────────────────────────────────────────
    st.subheader("💬 Community Sentiment")
    sentiment = analysis.get("sentiment", {})
    sc1, sc2, sc3 = st.columns(3)
    sc1.metric("Positive", f"{sentiment.get('positive_pct', 0)}%")
    sc2.metric("Neutral", f"{sentiment.get('neutral_pct', 0)}%")
    sc3.metric("Negative", f"{sentiment.get('negative_pct', 0)}%")
    if sentiment.get("summary"):
        st.info(sentiment["summary"])

    # ── Pain Points & Questions ────────────────────────────────────
    col_l, col_r = st.columns(2)
    with col_l:
        st.subheader("😣 Top Pain Points")
        for i, pain in enumerate(analysis.get("pain_points", []), 1):
            st.write(f"{i}. {pain}")
    with col_r:
        st.subheader("❓ Top Questions Asked")
        for i, q in enumerate(analysis.get("questions_asked", []), 1):
            st.write(f"{i}. {q}")

    # ── Content Ideas ──────────────────────────────────────────────
    st.subheader(f"✨ Content Ideas for {brand_name}")
    ideas = analysis.get("content_ideas", [])
    for idea in ideas:
        with st.container(border=True):
            st.markdown(f"**{idea['title']}**")
            badge = f"`{idea.get('format', 'Content')}`"
            st.markdown(badge)
            st.write(idea.get("rationale", ""))

    # ── Source Posts ───────────────────────────────────────────────
    st.subheader("🔗 Source Posts")
    with st.expander(f"View all {len(posts)} fetched posts from r/{subreddit}"):
        for p in sorted(posts, key=lambda x: x["score"], reverse=True):
            st.markdown(
                f"[{p['title']}]({p['url']})  \n"
                f"`{p['date']}` · ⬆ {p['score']} · 💬 {p['comments']}"
            )
            st.divider()

    st.caption("Powered by Reddit public data · Gemini on Vertex AI")
