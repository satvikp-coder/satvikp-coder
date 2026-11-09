"""Build profile stat cards (SVG) from the GitHub API. No third-party services.

Env:
  GITHUB_TOKEN    token used for API calls (required)
  STATS_USER      GitHub login (default: satvikp-coder)
  INCLUDE_PRIVATE set to 1 to also count private repos (needs a token that can read them)
Output: assets/stats-card.svg, assets/langs-card.svg, assets/repos-card.svg
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.request
from html import escape
from pathlib import Path

USER = os.environ.get("STATS_USER", "satvikp-coder")
TOKEN = os.environ["GITHUB_TOKEN"]
PRIVATE = os.environ.get("INCLUDE_PRIVATE") == "1"
OUT = Path(__file__).resolve().parents[1] / "assets"
EXCLUDE_LANGS = {"Jupyter Notebook"}  # notebook outputs inflate byte counts
W, H = 495, 195


def api(path, data=None, retries=6):
    url = path if path.startswith("http") else "https://api.github.com" + path
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {TOKEN}", "Accept": "application/vnd.github+json",
        "User-Agent": "profile-stats"})
    for _ in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                if r.status == 202:  # stats still being computed
                    time.sleep(4)
                    continue
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            if e.code in (202, 502, 503):
                time.sleep(4)
                continue
            if e.code in (404, 409, 451):
                return None
            raise
    return None


def graphql(query):
    return api("https://api.github.com/graphql", {"query": query})["data"]


def repos():
    out, page = [], 1
    while True:
        kind = "all" if PRIVATE else "public"
        chunk = api(f"/user/repos?per_page=100&page={page}&visibility={kind}&affiliation=owner") \
            if PRIVATE else api(f"/users/{USER}/repos?per_page=100&page={page}&type=owner")
        if not chunk:
            break
        out += chunk
        page += 1
    return [r for r in out if not r["fork"]]


def contributions(created):
    tot = dict(commits=0, prs=0, issues=0, reviews=0)
    start = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
    now = dt.datetime.now(dt.timezone.utc)
    while start < now:
        end = min(start + dt.timedelta(days=364), now)
        q = ('{user(login:"%s"){contributionsCollection(from:"%s",to:"%s"){'
             'totalCommitContributions totalPullRequestContributions '
             'totalIssueContributions totalPullRequestReviewContributions '
             'restrictedContributionsCount}}}') % (
            USER, start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        c = graphql(q)["user"]["contributionsCollection"]
        tot["commits"] += c["totalCommitContributions"] + (c["restrictedContributionsCount"] if PRIVATE else 0)
        tot["prs"] += c["totalPullRequestContributions"]
        tot["issues"] += c["totalIssueContributions"]
        tot["reviews"] += c["totalPullRequestReviewContributions"]
        start = end
    return tot


def fmt(n):
    return f"{n:,}"


def card(title, body, note=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
            f'role="img" aria-label="{escape(title)}">'
            '<style>text{font-family:Segoe UI,Ubuntu,Helvetica,Arial,sans-serif;fill:#24292f}'
            '.t{font-size:18px;font-weight:600;fill:#0969da}.l{font-size:13px;fill:#57606a}'
            '.v{font-size:13px;font-weight:600}.f{font-size:10px;fill:#8c959f}</style>'
            f'<rect x="0.5" y="0.5" width="{W-1}" height="{H-1}" rx="6" fill="#ffffff" stroke="#d0d7de"/>'
            f'<text class="t" x="25" y="35">{escape(title)}</text>{body}'
            f'<text class="f" x="25" y="{H-10}">Updated {dt.date.today().isoformat()}'
            f'{" | incl. private repos" if PRIVATE else " | public repos only"}{note}</text></svg>')


def bars(rows, total):
    """rows: (label, value, display)"""
    out, y = [], 62
    for label, val, shown in rows:
        w = 0 if total == 0 else max(2, int(190 * val / total))
        out.append(f'<text class="l" x="25" y="{y}">{escape(label)}</text>'
                   f'<rect x="165" y="{y-10}" width="{w}" height="10" rx="3" fill="#0969da"/>'
                   f'<text class="v" x="{165+w+8}" y="{y}">{escape(shown)}</text>')
        y += 24
    return "".join(out)


def main():
    OUT.mkdir(exist_ok=True)
    user = api(f"/users/{USER}")
    rs = repos()
    contrib = contributions(user["created_at"])

    adds = dels = commits_all = 0
    langs, per_repo = {}, []
    for r in rs:
        name = r["full_name"]
        for lang, n in (api(f"/repos/{name}/languages") or {}).items():
            if lang not in EXCLUDE_LANGS:
                langs[lang] = langs.get(lang, 0) + n
        stats = api(f"/repos/{name}/stats/contributors") or []
        for s in stats:
            if s["author"] and s["author"]["login"].lower() == USER.lower():
                a = sum(w["a"] for w in s["weeks"])
                d = sum(w["d"] for w in s["weeks"])
                adds += a
                dels += d
                commits_all += s["total"]
                per_repo.append((r["name"], s["total"]))
    stars = sum(r["stargazers_count"] for r in rs)

    stats_body = ""
    left = [("Commits (all repos)", fmt(commits_all)), ("Pull requests", fmt(contrib["prs"])),
            ("Issues", fmt(contrib["issues"])), ("Code reviews", fmt(contrib["reviews"])),
            ("Repositories", fmt(len(rs)))]
    right = [("Lines added", "+" + fmt(adds)), ("Lines deleted", "-" + fmt(dels)),
             ("Net lines", fmt(adds - dels)), ("Stars earned", fmt(stars)),
             ("Followers", fmt(user["followers"]))]
    y = 62
    for (l1, v1), (l2, v2) in zip(left, right):
        stats_body += (f'<text class="l" x="25" y="{y}">{l1}</text><text class="v" x="165" y="{y}">{v1}</text>'
                       f'<text class="l" x="265" y="{y}">{l2}</text><text class="v" x="400" y="{y}">{v2}</text>')
        y += 24
    (OUT / "stats-card.svg").write_text(card("GitHub stats", stats_body, " | lines include data and lock files"), encoding="utf-8")

    total = sum(langs.values())
    top = sorted(langs.items(), key=lambda kv: -kv[1])[:6]
    rows = [(k, v, f"{100*v/total:.1f}%") for k, v in top] if total else []
    (OUT / "langs-card.svg").write_text(card("Top languages", bars(rows, top[0][1] if top else 0)), encoding="utf-8")

    best = sorted(per_repo, key=lambda kv: -kv[1])[:6]
    rows = [(k, v, fmt(v)) for k, v in best]
    (OUT / "repos-card.svg").write_text(card("Most active repos (my commits)", bars(rows, best[0][1] if best else 0)), encoding="utf-8")
    print("commits", commits_all, "adds", adds, "dels", dels, "repos", len(rs), "langs", [k for k, _ in top])


if __name__ == "__main__":
    main()
