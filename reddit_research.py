#!/usr/bin/env python3
"""Read-only Reddit search counts for personal problem research.

Uses application-only OAuth (client credentials). It never posts, comments,
votes, messages or moderates. It collects no usernames: only post titles,
links, scores, comment counts and dates. Posts that have been deleted or
removed are dropped. Results are written to local files only.

Credentials are read from ~/.config/reddit-research.env (or environment
variables) and are never printed or written anywhere else.
"""

import argparse
import base64
import datetime as dt
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENV_FILE = Path.home() / ".config" / "reddit-research.env"
TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
API_BASE = "https://oauth.reddit.com"
HERE = Path(__file__).resolve().parent

PAUSE_SECONDS = 1.0   # pause between requests, well under the rate limit
PAGE_SIZE = 100       # Reddit's maximum per request
MAX_PAGES = 3         # Reddit search returns at most ~250 results anyway
MAX_RETRIES = 3
KEEP_DAYS = 30        # results older than this are deleted at the start of each run


def load_env_file(path: Path) -> None:
    """Load KEY=VALUE lines into the environment without overriding existing values."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"Missing {name}. Add it to {ENV_FILE} (see README).")
    return value


def get_token(client_id: str, client_secret: str, user_agent: str) -> str:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    request = urllib.request.Request(
        TOKEN_URL,
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={"Authorization": f"Basic {credentials}", "User-Agent": user_agent},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = json.load(response)
    except urllib.error.HTTPError as error:
        sys.exit(f"Could not get an access token (HTTP {error.code}). Check the client ID and secret.")
    except (urllib.error.URLError, TimeoutError) as error:
        sys.exit(f"Could not reach Reddit to get an access token: {getattr(error, 'reason', error)}")
    if "access_token" not in body:
        sys.exit(f"Could not get an access token: {body.get('error', 'unknown error')}")
    return body["access_token"]


def api_get(path: str, params: dict, token: str, user_agent: str) -> dict:
    url = f"{API_BASE}{path}?{urllib.parse.urlencode(params)}"
    headers = {"Authorization": f"Bearer {token}", "User-Agent": user_agent}
    for attempt in range(1, MAX_RETRIES + 1):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                remaining = float(response.headers.get("x-ratelimit-remaining", "100"))
                reset = float(response.headers.get("x-ratelimit-reset", "0"))
                body = json.load(response)
            if remaining < 5:
                time.sleep(reset + 1)
            return body
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < MAX_RETRIES:
                wait = float(error.headers.get("retry-after") or error.headers.get("x-ratelimit-reset") or 60)
                print(f"  rate limited; waiting {wait:.0f}s", file=sys.stderr)
                time.sleep(wait + 1)
                continue
            raise


def is_live(post: dict) -> bool:
    """False for posts that have been deleted by their author or removed by moderators."""
    if post.get("removed_by_category"):
        return False
    return post.get("selftext") not in ("[deleted]", "[removed]") and post.get("title") not in ("[deleted]", "[removed]")


def as_phrase(query: str) -> str:
    """Quote multi-word queries so Reddit matches the exact phrase, not the words separately."""
    if " " in query and not query.startswith('"'):
        return f'"{query}"'
    return query


def search(subreddit: str, query: str, time_filter: str, token: str, user_agent: str) -> list:
    posts, after = [], None
    for _ in range(MAX_PAGES):
        params = {
            "q": as_phrase(query),
            "restrict_sr": "1",
            "sort": "top",
            "t": time_filter,
            "limit": PAGE_SIZE,
            "raw_json": "1",
        }
        if after:
            params["after"] = after
        listing = api_get(f"/r/{subreddit}/search", params, token, user_agent)
        children = listing.get("data", {}).get("children", [])
        for child in children:
            # Only posts (t3). A missing subreddit can redirect to a list of subreddits (t5).
            if child.get("kind") != "t3":
                continue
            data = child.get("data", {})
            if not is_live(data):
                continue
            # Deliberately no author or other user fields.
            posts.append({
                "title": data.get("title", ""),
                "score": data.get("score", 0),
                "num_comments": data.get("num_comments", 0),
                "created": dt.datetime.fromtimestamp(data.get("created_utc", 0), dt.timezone.utc).date().isoformat(),
                "link": f"https://www.reddit.com{data.get('permalink', '')}",
            })
        after = listing.get("data", {}).get("after")
        time.sleep(PAUSE_SECONDS)
        if not after:
            break
    return posts


def summarise(posts: list, top: int) -> dict:
    scores = [p["score"] for p in posts]
    return {
        "posts": len(posts),
        "total_upvotes": sum(scores),
        "total_comments": sum(p["num_comments"] for p in posts),
        "median_upvotes": statistics.median(scores) if scores else 0,
        "top_posts": sorted(posts, key=lambda p: p["score"], reverse=True)[:top],
    }


def md_escape(text: str) -> str:
    """Escape characters that would break a Markdown table cell or link."""
    for char in ("\\", "|", "[", "]"):
        text = text.replace(char, "\\" + char)
    return text.replace("\n", " ")


def prune_old_results(out: Path, keep_days: int) -> None:
    """Delete saved results older than keep_days, so titles of later-deleted posts aren't kept."""
    cutoff = time.time() - keep_days * 86400
    for path in out.glob("*-*.*"):
        if path.suffix in (".json", ".md") and path.stat().st_mtime < cutoff:
            path.unlink()


def write_markdown(path: Path, run: dict) -> None:
    lines = [
        f"# Reddit problem research: {run['date']}",
        "",
        f"Time window: past {run['time_filter']}. Counts are lower bounds: Reddit search returns at most ~250 results per query. Deleted and removed posts are excluded.",
        "",
        "| Problem | Subreddit | Query | Posts | Upvotes | Comments | Median upvotes | Top post |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for result in run["results"]:
        query = md_escape(as_phrase(result["query"]))
        if "error" in result:
            lines.append(f"| {result['problem']} | r/{result['subreddit']} | {query} | error: {md_escape(result['error'])} | | | | |")
            continue
        top = result["top_posts"][0] if result["top_posts"] else None
        top_cell = f"[{md_escape(top['title'][:70])}]({top['link']}) ({top['score']})" if top else ""
        lines.append(
            f"| {result['problem']} | r/{result['subreddit']} | {query} | {result['posts']} | "
            f"{result['total_upvotes']} | {result['total_comments']} | {result['median_upvotes']} | {top_cell} |"
        )
    path.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Reddit search counts for problem research.")
    parser.add_argument("--queries", default=str(HERE / "queries.json"), help="JSON file of searches to run")
    parser.add_argument("--time", default="year", choices=["day", "week", "month", "year", "all"], help="time window")
    parser.add_argument("--top", type=int, default=5, help="top posts to keep per search")
    parser.add_argument("--out", default=str(HERE / "results"), help="output folder")
    parser.add_argument("--only", help="run only this problem ID, e.g. P13")
    parser.add_argument("--dry-run", action="store_true", help="list the searches without calling Reddit")
    args = parser.parse_args()

    queries = json.loads(Path(args.queries).read_text())
    if args.only:
        queries = [q for q in queries if q["problem"] == args.only]
        if not queries:
            sys.exit(f"No searches found for {args.only} in {args.queries}.")

    if args.dry_run:
        for q in queries:
            print(f"{q['problem']}: r/{q['subreddit']} {as_phrase(q['query'])}")
        print(f"{len(queries)} searches, up to {len(queries) * MAX_PAGES} requests.")
        return

    load_env_file(ENV_FILE)
    user_agent = require_env("REDDIT_USER_AGENT")
    token = get_token(require_env("REDDIT_CLIENT_ID"), require_env("REDDIT_CLIENT_SECRET"), user_agent)

    run = {"date": dt.date.today().isoformat(), "time_filter": args.time, "results": []}
    for q in queries:
        print(f"{q['problem']}: r/{q['subreddit']} {as_phrase(q['query'])}", file=sys.stderr)
        try:
            posts = search(q["subreddit"], q["query"], args.time, token, user_agent)
            run["results"].append({**q, **summarise(posts, args.top)})
        except urllib.error.HTTPError as error:
            run["results"].append({**q, "error": f"HTTP {error.code}"})
        except (urllib.error.URLError, TimeoutError) as error:
            run["results"].append({**q, "error": f"network: {getattr(error, 'reason', error)}"})

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    prune_old_results(out, KEEP_DAYS)
    stem = out / f"{run['date']}-{args.time}"
    stem.with_suffix(".json").write_text(json.dumps(run, indent=2))
    write_markdown(stem.with_suffix(".md"), run)
    print(f"Wrote {stem.with_suffix('.md')} and {stem.with_suffix('.json')}")


if __name__ == "__main__":
    main()
