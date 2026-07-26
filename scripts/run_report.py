"""Run the real pipeline from a terminal — end-to-end check and sample generator.

Exercises exactly the code path the Streamlit app uses, with no Streamlit involved.

    python scripts/run_report.py \
        --query "Productivity struggles freelance designers have juggling multiple clients" \
        --profile "I run a Notion template shop for freelance designers" \
        --platform linkedin --days 7 \
        --json sample_report.json --markdown report.md --sample

Pass --subreddits to skip discovery entirely and analyse specific communities instead
— useful for deterministic regeneration and for debugging one community in isolation.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
import reddit_data  # noqa: E402
import report_export  # noqa: E402


def _manual_discover(names):
    """Build a `discover` callable that skips discovery for a fixed set of names.

    Communities picked this way were not run past the model or checked against the
    archive, so they carry a reason that says so plainly rather than pretending to
    be a discovery result.
    """
    communities = [
        {"name": name, "reason": "chosen manually", "subscribers": 0, "checked": False}
        for name in names
    ]

    def discover(query, profile):
        return {"communities": communities, "considered": [c["name"] for c in communities]}

    return discover


def _parse_subreddits(raw):
    names = []
    seen = set()
    for chunk in raw.split(","):
        if not chunk.strip():
            continue
        name = reddit_data.normalize_subreddit(chunk)
        if name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    if not names:
        raise ValueError("Please pass at least one subreddit to --subreddits.")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Reddit content ideas report.")
    parser.add_argument("--query", required=True, help="what you want content ideas about")
    parser.add_argument("--profile", default="", help="optional: who you are / your brand")
    parser.add_argument("--platform", default="linkedin", choices=list(pipeline.PLATFORMS.keys()))
    parser.add_argument("--days", type=int, default=7, choices=list(pipeline.TIME_WINDOWS))
    parser.add_argument("--json", dest="json_path", help="write the full bundle here")
    parser.add_argument("--markdown", dest="markdown_path", help="write the Markdown report here")
    parser.add_argument("--sample", action="store_true", help="mark the bundle as a sample report")
    parser.add_argument(
        "--subreddits",
        help="comma-separated subreddit names; when given, discovery is skipped entirely "
             "and these communities are analysed directly",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )

    checked = pipeline.validate_inputs(args.query, args.profile, args.platform, args.days)
    if checked["errors"]:
        for error in checked["errors"]:
            print("input error: {}".format(error))
        return 2

    discover = None
    if args.subreddits:
        try:
            names = _parse_subreddits(args.subreddits)
        except ValueError as exc:
            print("input error: {}".format(exc))
            return 2
        discover = _manual_discover(names)

    try:
        bundle = pipeline.run_report(
            checked["query"],
            checked["profile"],
            checked["platform"],
            checked["days"],
            progress=lambda message: print("  · {}".format(message)),
            discover=discover,
        )
    except pipeline.RunError as exc:
        print("run failed: {}".format(exc))
        return 1
    except ValueError as exc:
        print("run failed: {}".format(exc))
        return 1

    if args.sample:
        bundle["is_sample"] = True

    markdown = report_export.report_markdown(bundle)

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle, ensure_ascii=False, indent=1)
        print("wrote {}".format(args.json_path))
    if args.markdown_path:
        with open(args.markdown_path, "w", encoding="utf-8") as handle:
            handle.write(markdown)
        print("wrote {}".format(args.markdown_path))

    sample = bundle["sample"]
    sentiment = bundle["stats"]["sentiment"]
    print("\n--- end-to-end summary ---")
    print("platform:            {}".format(bundle["inputs"]["platform_label"]))
    print("communities:         {}".format(", ".join(bundle["inputs"]["subreddits"])))
    print("communities found:   {}".format(
        ", ".join("r/{}".format(c["name"]) for c in bundle["discovery"]["communities"])))
    print("posts fetched:       {}".format(sample["posts_fetched"]))
    print("comments fetched:    {}".format(sample["comments_fetched"]))
    print("items analysed:      {} ({} posts + {} comments)".format(
        sample["items_analysed"], sample["posts_analysed"], sample["comments_analysed"]))
    print("sentiment counts:    {}".format(sentiment["counts"]))
    print("sentiment pct:       {} (sum {})".format(
        sentiment["percentages"], sum(sentiment["percentages"].values())))
    print("topics:              {}".format(len(bundle["analysis"]["topics"])))
    print("pain points:         {}".format(len(bundle["analysis"]["pain_points"])))
    print("questions:           {}".format(len(bundle["analysis"]["questions"])))
    print("content ideas:       {}".format(len(bundle["analysis"]["content_ideas"])))
    print("markdown chars:      {}".format(len(markdown)))
    print("elapsed:             {}s".format(bundle["elapsed_seconds"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
