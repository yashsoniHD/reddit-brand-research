"""Run the real pipeline from a terminal: end-to-end check and sample generator.

Exercises exactly the code path the web app uses, with no web app involved.

    python scripts/run_report.py \
        --query "Productivity struggles freelance designers have juggling multiple clients" \
        --profile "I run a Notion template shop for freelance designers" \
        --json sample_report.json --markdown report.md --sample

Pass --subreddits to skip discovery entirely and analyse specific communities instead,
which is useful for deterministic regeneration and for debugging one community.
Pass --content-types to keep every idea inside the formats you name.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pipeline  # noqa: E402
import reddit_data  # noqa: E402
import report_export  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Reddit content ideas report.")
    parser.add_argument("--query", required=True, help="what you want content ideas about")
    parser.add_argument("--profile", default="", help="optional: who you are / your brand")
    parser.add_argument(
        "--days", type=int, default=reddit_data.FETCH_WINDOW_DAYS,
        choices=list(pipeline.TIME_WINDOWS),
        help="time range to read (advanced; default the full window)",
    )
    parser.add_argument(
        "--no-relevance", action="store_true",
        help="skip the relevance triage call and select by engagement and recency only",
    )
    parser.add_argument("--json", dest="json_path", help="write the full bundle here")
    parser.add_argument("--markdown", dest="markdown_path", help="write the Markdown report here")
    parser.add_argument("--sample", action="store_true", help="mark the bundle as a sample report")
    parser.add_argument(
        "--subreddits",
        help="comma-separated subreddit names; when given, discovery is skipped entirely "
             "and these communities are analysed directly",
    )
    parser.add_argument(
        "--content-types", dest="content_types", nargs="+",
        choices=list(pipeline.CONTENT_TYPES),
        help="keep every idea inside these formats (advanced; default a mix)",
    )
    parser.add_argument(
        "--timeout", type=float,
        help="seconds before the run stops starting new work and reports what it has",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout
    )

    checked = pipeline.validate_inputs(
        args.query, args.profile,
        days=args.days, subreddits=args.subreddits,
        relevance_filter=not args.no_relevance,
        content_types=args.content_types,
    )
    if checked["errors"]:
        for error in checked["errors"]:
            print("input error: {}".format(error))
        return 2

    deadline = None if args.timeout is None else time.monotonic() + args.timeout
    try:
        bundle = pipeline.run_report(
            checked["query"],
            checked["profile"],
            days=checked["days"],
            subreddits=checked["subreddits"],
            relevance_filter=checked["relevance_filter"],
            content_types=checked["content_types"],
            progress=lambda message: print("  · {}".format(message)),
            deadline=deadline,
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
    print("bundle version:      {}".format(bundle["bundle_version"]))
    print("report title:        {}".format(bundle["analysis"]["report_title"]))
    print("query:               {}".format(bundle["inputs"]["query"]))
    print("content types:       {}".format(
        ", ".join(bundle["inputs"].get("content_types") or []) or "a mix"))
    print("communities:         {} (chosen: {})".format(
        ", ".join(bundle["inputs"]["subreddits"]), bundle["inputs"]["discovery"]))
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
    print("analysed engagement: {}".format(bundle["stats"]["engagement"]["analysed"]))
    print("models used:         {}".format(", ".join(bundle["models"])))
    print("markdown chars:      {}".format(len(markdown)))
    print("elapsed:             {}s".format(bundle["elapsed_seconds"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
