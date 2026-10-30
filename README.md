# reddit-problem-research

A small personal script that measures how often everyday problems come up in public Reddit communities. For example: how many posts in the past year mention "service charge" in r/LeaseholdUK, and how much discussion they get.

## What it does

- Runs keyword searches in a fixed list of public subreddits (`queries.json`).
- Records, per search: number of posts, total and median upvotes, total comments, and the titles and links of the top few posts.
- Writes the results to local files (`results/DATE-WINDOW.md` and `.json`).

## What it doesn't do

- **Read-only.** It never posts, comments, votes, messages, moderates or changes anything on Reddit.
- **No user data.** It doesn't collect usernames, profiles or comment histories.
- **Respects deletions.** Posts deleted by their authors or removed by moderators are excluded. Each run fetches fresh data, and saved results older than 30 days are deleted automatically, so later deletions don't linger.
- **Light use.** About 1 request per second, up to 3 requests per search (42 for the current 14 searches) plus one login request, run by hand roughly once a week. It backs off if Reddit signals a rate limit.
- **No redistribution.** Results stay on the owner's computer. Nothing is published, sold or used to train AI models.

## How it works

- Application-only OAuth (client credentials) for a Reddit "script" app. No user login.
- Python 3 standard library only; no dependencies.
- Endpoints used: `POST https://www.reddit.com/api/v1/access_token` and `GET https://oauth.reddit.com/r/{subreddit}/search`.

## Setup

1. Get Reddit API approval and create a "script" app at <https://www.reddit.com/prefs/apps>.
2. Create `~/.config/reddit-research.env` (keep it private: `chmod 600`), outside this repository:

   ```
   REDDIT_CLIENT_ID=your_client_id
   REDDIT_CLIENT_SECRET=your_secret
   REDDIT_USER_AGENT=macos:problem-research:v0.1 (by /u/YOUR_REDDIT_USERNAME)
   ```

## Usage

```
python3 reddit_research.py --dry-run        # list the searches, no network
python3 reddit_research.py                  # run all searches (past year)
python3 reddit_research.py --only P13       # one problem only
python3 reddit_research.py --time month     # past month instead of year
```

Multi-word queries are searched as exact phrases (e.g. `"service charge"`). Counts are lower bounds: Reddit search returns at most about 250 results per query.

## Data and terms

Used under Reddit's Developer Terms, Data API Terms and Responsible Builder Policy, for personal, non-commercial research.
