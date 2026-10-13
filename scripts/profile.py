"""Generate the CYBER // MIDNIGHT GitHub profile from public GitHub data."""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- PROFILE:GENERATED:START -->"
END = "<!-- PROFILE:GENERATED:END -->"
COLORS = {"bg": "#0b1020", "panel": "#111b2d", "line": "#25405a", "text": "#eaf5fa", "muted": "#9eb2c3", "cyan": "#36d8ee", "violet": "#a99aff", "green": "#62d7a2"}
LANG_EXT = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".java": "Java", ".c": "C", ".cpp": "C++", ".cc": "C++", ".sql": "SQL", ".ipynb": "Jupyter Notebook"}
GRAPHQL = """query($login:String!, $from:DateTime!, $to:DateTime!) { user(login:$login) { login contributionsCollection(from:$from,to:$to) { contributionCalendar { totalContributions weeks { contributionDays { date contributionCount } } } totalCommitContributions totalPullRequestContributions } } }"""


class APIError(RuntimeError):
    pass


class GitHub:
    def __init__(self, token: str, max_requests: int = 260):
        if not token:
            raise APIError("GITHUB_TOKEN or GH_TOKEN is required")
        self.token, self.max_requests, self.requests = token, max_requests, 0
        self.cache = {}

    def request(self, path: str, payload: dict | None = None):
        key = (path, json.dumps(payload, sort_keys=True) if payload else "")
        if key in self.cache:
            return self.cache[key]
        if self.requests >= self.max_requests:
            raise APIError("API request budget exceeded; reduce configured limits")
        url = "https://api.github.com/" + path.lstrip("/")
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "User-Agent": "satvikp-profile-generator", "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        for attempt in range(4):
            self.requests += 1
            try:
                with urllib.request.urlopen(req, timeout=20) as response:
                    result = json.load(response)
                    remaining = int(response.headers.get("X-RateLimit-Remaining", "100"))
                    if remaining < 20:
                        raise APIError("GitHub API rate limit nearly exhausted")
                    if isinstance(result, dict) and result.get("errors"):
                        raise APIError("GitHub GraphQL error: " + str(result["errors"]))
                    self.cache[key] = result
                    return result
            except urllib.error.HTTPError as exc:
                body = exc.read(500).decode("utf-8", "replace")
                if exc.code == 403 and exc.headers.get("X-RateLimit-Remaining") == "0":
                    raise APIError("GitHub API rate limit exhausted") from exc
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise APIError(f"GitHub API {exc.code} for {path}: {body}") from exc
                retry = exc.headers.get("Retry-After")
                time.sleep(min(30, int(retry) if retry and retry.isdigit() else 2 ** attempt))
            except (urllib.error.URLError, TimeoutError) as exc:
                if attempt == 3:
                    raise APIError(f"GitHub API connection error for {path}: {exc}") from exc
                time.sleep(2 ** attempt)
        raise APIError("Unreachable retry state")

    def pages(self, path: str, max_pages: int = 5, bounded: bool = False):
        output = []
        for page in range(1, max_pages + 1):
            separator = "&" if "?" in path else "?"
            values = self.request(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(values, list):
                raise APIError(f"Expected paginated list from {path}")
            output.extend(values)
            if len(values) < 100:
                break
        else:
            if not bounded:
                raise APIError(f"Pagination budget exceeded for {path}")
        return output


def parsedate(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def author_matches(commit: dict, login: str, aliases: set[str]) -> bool:
    author = commit.get("author") or {}
    if author.get("login", "").lower() == login.lower():
        return True
    # An alias must be explicitly configured. Never trust a display name alone.
    email = ((commit.get("commit") or {}).get("author") or {}).get("email", "").lower()
    return bool(email and email in aliases)


def personal_commit(commit: dict, login: str, aliases: set[str], profile_repo: str) -> bool:
    if not author_matches(commit, login, aliases):
        return False
    identity = (commit.get("author") or {}).get("login", "").lower()
    message = ((commit.get("commit") or {}).get("message") or "").lower()
    if identity.endswith("[bot]") or "[bot]" in identity:
        return False
    if commit.get("repository", "").lower() == profile_repo.lower() and message.startswith("chore(profile): refresh"):
        return False
    return True


def streaks(days: list[dict], today) -> tuple[int, int]:
    counts = {datetime.strptime(d["date"], "%Y-%m-%d").date(): int(d["contributionCount"]) for d in days}
    current = 0
    date = today if counts.get(today, 0) else today - timedelta(days=1)
    while counts.get(date, 0):
        current += 1
        date -= timedelta(days=1)
    longest = running = 0
    for date in sorted(counts):
        running = running + 1 if counts[date] else 0
        longest = max(longest, running)
    return current, longest


def select_repositories(repos: list[dict], events: list[dict], login: str, limit: int, public_names: set[str]) -> list[str]:
    owned = [r for r in repos if not r.get("fork") and not r.get("archived") and r.get("size", 0) > 0 and r.get("name", "").lower() != login.lower()]
    owned.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
    names = [r["full_name"] for r in owned]
    for event in events:
        repo = (event.get("repo") or {}).get("name", "")
        if repo and repo in public_names and repo not in names and event.get("type") in ("PushEvent", "PullRequestEvent"):
            names.append(repo)
    return names[:limit]


def collect(api: GitHub, config: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    login = config["username"]
    if os.getenv("GITHUB_ACTIONS") == "true":
        if os.getenv("GITHUB_REPOSITORY", "").lower() != (login + "/" + login).lower():
            raise APIError("Workflow repository differs from configured profile owner")
    else:
        viewer = api.request("user")
        if viewer.get("login", "").lower() != login.lower():
            raise APIError(f"Authenticated user {viewer.get('login')} differs from configured user {login}")
    user = api.request("users/" + login)
    repos = api.pages("users/" + login + "/repos?type=public&sort=updated", 2)
    repos = [r for r in repos if not r.get("private")]
    events = api.pages("users/" + login + "/events/public", 3)
    public_names = {r["full_name"] for r in repos}
    event_names = {(e.get("repo") or {}).get("name", "") for e in events} - public_names - {""}
    for name in sorted(event_names):
        try:
            repository = api.request("repos/" + name)
        except APIError as exc:
            if "404" in str(exc):
                continue
            raise
        if not repository.get("private") and repository.get("visibility", "public") == "public":
            public_names.add(name)
    from_date = now - timedelta(days=365)
    graph = api.request("graphql", {"query": GRAPHQL, "variables": {"login": login, "from": from_date.isoformat(), "to": now.isoformat()}})
    collection = (((graph.get("data") or {}).get("user") or {}).get("contributionsCollection"))
    if not collection:
        raise APIError("Missing GitHub contribution data")
    calendar = collection["contributionCalendar"]
    days = [day for week in calendar["weeks"] for day in week["contributionDays"]]
    aliases = {x.lower() for x in config.get("author_emails", [])}
    selected = select_repositories(repos, events, login, int(config.get("repository_limit", 20)), public_names)
    cutoff30, cutoff7 = now - timedelta(days=30), now - timedelta(days=7)
    detail_limit = int(config.get("commit_detail_limit", 120))
    per_repo = int(config.get("commit_list_limit_per_repository", 100))
    commits = []
    truncated = False
    for repo in selected:
        if len(commits) >= detail_limit:
            truncated = True
            break
        listed_by_sha = {}
        for author in [login, *sorted(aliases)]:
            query = urllib.parse.urlencode({"author": author, "since": cutoff30.isoformat(), "until": now.isoformat()})
            try:
                author_commits = api.pages(f"repos/{repo}/commits?{query}", max(1, (per_repo + 99) // 100), bounded=True)
            except APIError as exc:
                if "404" in str(exc) or "409" in str(exc):
                    continue  # A public event may refer to a deleted/empty repository.
                raise
            if len(author_commits) >= per_repo:
                truncated = True
            for item in author_commits[:per_repo]:
                listed_by_sha[item["sha"]] = item
        listed = list(listed_by_sha.values())
        if len(listed) >= per_repo:
            truncated = True
        for item in listed[:per_repo]:
            item["repository"] = repo
            if not personal_commit(item, login, aliases, login + "/" + login):
                continue
            date = parsedate(((item.get("commit") or {}).get("author") or {})["date"])
            if date < cutoff30 or date > now:
                continue
            if len(commits) >= detail_limit:
                truncated = True
                break
            detail = api.request(f"repos/{repo}/commits/{item['sha']}")
            stats = detail.get("stats")
            if not isinstance(stats, dict) or "additions" not in stats or "deletions" not in stats:
                raise APIError(f"Missing diff statistics for {repo}@{item['sha']}")
            commits.append({"repo": repo, "sha": item["sha"], "date": date, "additions": int(stats["additions"]), "deletions": int(stats["deletions"]), "files": detail.get("files") or []})
    recent_repos = Counter(c["repo"] for c in commits)
    file_languages = Counter()
    for commit in commits:
        for file in commit["files"]:
            suffix = Path(file.get("filename", "")).suffix.lower()
            if suffix in LANG_EXT:
                file_languages[LANG_EXT[suffix]] += 1
    repo_languages = Counter()
    for repo in repos:
        if not repo.get("fork") and repo.get("language"):
            repo_languages[repo["language"]] += 1
    public_events = []
    seen = set()
    for event in events:
        repo = (event.get("repo") or {}).get("name", "")
        if repo not in public_names:
            continue
        if repo.lower() == (login + "/" + login).lower():
            continue
        kind = event.get("type")
        action = (event.get("payload") or {}).get("action")
        label = {"PushEvent": "Pushed code", "ReleaseEvent": "Published a release", "CreateEvent": "Created a repository"}.get(kind)
        if kind == "PullRequestEvent" and action in ("opened", "merged"):
            label = "Merged a pull request" if action == "merged" else "Opened a pull request"
        if not label or (label, repo) in seen or not repo:
            continue
        if kind == "CreateEvent" and (event.get("payload") or {}).get("ref_type") != "repository":
            continue
        seen.add((label, repo))
        public_events.append({"label": label, "repo": repo, "date": event["created_at"][:10]})
        if len(public_events) == 5:
            break
    current, longest = streaks(days, now.date())
    return {"date": now.date().isoformat(), "user": login, "public_repos": len(repos), "stars": sum(r.get("stargazers_count") or 0 for r in repos if not r.get("fork")), "contributions": calendar["totalContributions"], "contribution_commits": collection["totalCommitContributions"], "contribution_prs": collection["totalPullRequestContributions"], "current_streak": current, "longest_streak": longest, "calendar": days, "commits_7": sum(c["date"] >= cutoff7 for c in commits), "commits_30": len(commits), "added_30": sum(c["additions"] for c in commits), "deleted_30": sum(c["deletions"] for c in commits), "truncated": truncated, "recent_repos": recent_repos.most_common(4), "changed_file_languages": file_languages.most_common(6), "repo_languages": repo_languages.most_common(6), "events": public_events, "repositories": {r["name"]: r for r in repos}}


def svg_open(width: int, height: int, title: str) -> list[str]:
    c = COLORS
    return [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" role="img" aria-label="{escape(title, quote=True)}">', f'<title>{escape(title)}</title>', f'<rect width="{width}" height="{height}" rx="22" fill="{c["bg"]}"/>', f'<rect x="1" y="1" width="{width-2}" height="{height-2}" rx="21" fill="none" stroke="{c["line"]}"/>']


def text_svg(x, y, value, size=16, color="text", weight=400, anchor=None):
    extra = f' text-anchor="{anchor}"' if anchor else ""
    return f'<text x="{x}" y="{y}" fill="{COLORS.get(color,color)}" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" font-weight="{weight}"{extra}>{escape(str(value))}</text>'


def render_hero(config: dict) -> str:
    s = svg_open(960, 280, "Satvik Patel, computer science student interested in cybersecurity and AI/ML")
    s += ['<defs><linearGradient id="glow"><stop stop-color="#36d8ee"/><stop offset="1" stop-color="#a99aff"/></linearGradient></defs>', '<path d="M640 0v70h72v45h125v80h123M750 0v46h100v76h110M625 280v-56h75v-64h114" fill="none" stroke="#1c4b66" stroke-width="1.5"/>', '<circle cx="712" cy="115" r="4" fill="#36d8ee"/><circle cx="850" cy="46" r="4" fill="#a99aff"/>', '<rect x="44" y="39" width="164" height="27" rx="13" fill="#15334a"/>', text_svg(57,58,"CYBER // MIDNIGHT",12,"cyan",700), text_svg(44,120,config["display_name"],52,"text",700), text_svg(46,158,"COMPUTER SCIENCE STUDENT",17,"cyan",600), text_svg(46,190,"Cybersecurity & AI/ML Enthusiast",21,"text",500), text_svg(46,225,"Building secure, intelligent, and useful systems",16,"muted"), '<rect x="44" y="247" width="872" height="2" fill="url(#glow)"/>', '<circle cx="888" cy="51" r="5" fill="#62d7a2"/>', '</svg>']
    return "\n".join(s) + "\n"


def render_stats(data: dict) -> str:
    s = svg_open(960, 280, "GitHub public profile statistics")
    s += [text_svg(36,47,"GITHUB // PUBLIC SIGNAL",18,"cyan",700), text_svg(922,46,"ROLLING 365 DAYS",12,"muted",500,"end")]
    items = [("PUBLIC REPOS",data["public_repos"]),("REPO STARS",data["stars"]),("CONTRIBUTIONS",data["contributions"]),("COMMIT CONTRIBUTIONS",data["contribution_commits"]),("PR CONTRIBUTIONS",data["contribution_prs"]),("CURRENT STREAK",str(data["current_streak"])+"d"),("LONGEST STREAK",str(data["longest_streak"])+"d"),("UPDATED",data["date"])]
    for i,(label,value) in enumerate(items):
        col,row = i%4,i//4
        x,y = 36+col*232,73+row*92
        s += [f'<rect x="{x}" y="{y}" width="216" height="78" rx="12" fill="{COLORS["panel"]}" stroke="{COLORS["line"]}"/>',text_svg(x+14,y+25,label,11,"muted",600),text_svg(x+14,y+59,value,29 if len(str(value))<8 else 19,"cyan" if i in (2,5) else "text",700)]
    s += [text_svg(36,267,"Streaks use visible GitHub contribution days; current streak may include yesterday.",11,"muted"),"</svg>"]
    return "\n".join(s)+"\n"


def render_languages(data: dict) -> str:
    s = svg_open(960, 240, "Languages across public owned repositories and recently changed files")
    s += [text_svg(36,45,"LANGUAGE SIGNAL",18,"cyan",700),text_svg(36,75,"Primary language of public owned repositories",13,"muted")]
    for i,(language,count) in enumerate(data["repo_languages"][:5]):
        y=100+i*25
        s += [text_svg(36,y+10,language,13,"text"),f'<rect x="190" y="{y}" width="{min(270, count*40)}" height="12" rx="6" fill="{COLORS["cyan"]}"/>',text_svg(475,y+11,count,12,"muted")]
    s += [text_svg(550,75,"Source files touched in analyzed commits (30d)",13,"muted")]
    for i,(language,count) in enumerate(data["changed_file_languages"][:5]):
        y=100+i*25
        s += [text_svg(550,y+10,language,13,"text"),f'<rect x="730" y="{y}" width="{min(140, count*11)}" height="12" rx="6" fill="{COLORS["violet"]}"/>',text_svg(884,y+11,count,12,"muted")]
    s += ["</svg>"]
    return "\n".join(s)+"\n"


def render_activity(data: dict) -> str:
    s=svg_open(960,310,"Development activity in analyzed public commits and 30-day contribution strip")
    s += [text_svg(36,45,"DEVELOPMENT ACTIVITY",18,"cyan",700),text_svg(922,44,"30-DAY BOUNDED SAMPLE",12,"muted",500,"end")]
    metrics=[("7D COMMITS",data["commits_7"]),("30D COMMITS",data["commits_30"]),("LINES ADDED", "+"+format(data["added_30"],",")),("LINES DELETED","−"+format(data["deleted_30"],","))]
    for i,(label,value) in enumerate(metrics):
        x=36+i*232
        s += [f'<rect x="{x}" y="68" width="216" height="75" rx="12" fill="{COLORS["panel"]}" stroke="{COLORS["line"]}"/>',text_svg(x+14,93,label,11,"muted",600),text_svg(x+14,126,value,27,"green" if i==2 else "text",700)]
    days=data["calendar"][-30:]
    s += [text_svg(36,180,"VISIBLE CONTRIBUTIONS // LAST 30 DAYS",12,"muted",700)]
    for i,day in enumerate(days):
        count=day["contributionCount"]
        color=COLORS["panel"] if count==0 else ("#245c65" if count<3 else "#278b91" if count<7 else COLORS["green"])
        s.append(f'<rect x="{36+i*29}" y="195" width="23" height="25" rx="5" fill="{color}"/>')
    s += [text_svg(36,246,"Each square is one calendar day; GitHub contributions include more than commits.",12,"muted"),text_svg(36,272,"Diff additions/deletions are changed lines, not original lines of code.",12,"muted"),text_svg(922,293,"SAMPLE CAPPED" if data["truncated"] else "ALL MATCHED COMMITS ANALYZED",11,"violet",600,"end"),"</svg>"]
    return "\n".join(s)+"\n"


def clean(value: str) -> str:
    return escape(value or "", quote=False).replace("|", "\\|").replace("\n", " ")


def generated_markdown(data: dict, config: dict) -> str:
    names=config["featured_repositories"]
    lines=["### Live GitHub signal", "", '<p align="center"><img src="assets/stats.svg" alt="Live GitHub statistics" width="960"></p>', '<p align="center"><img src="assets/languages.svg" alt="Language signal" width="960"></p>', "", "### Development activity", "", '<p align="center"><img src="assets/activity.svg" alt="Development activity and thirty day contribution strip" width="960"></p>', "", "**Recently active public repositories:** " + (", ".join(f"[{clean(name)}](https://github.com/{name})" for name,_ in data["recent_repos"]) if data["recent_repos"] else "No matched commits in the analysis window."), "", "### Selected work", ""]
    for name in names:
        repo=data["repositories"].get(name)
        if not repo or repo.get("fork") or repo.get("private"):
            continue
        descriptions={"sentinel":"Security controls and monitoring for autonomous browser agents.","demo-group7":"Heritage trip planning with a React client and Express/PostgreSQL backend.","phishing-slm-research":"A research plan comparing small language model approaches to phishing email detection.","Ras":"Local competition operations with inventory, transactions, and SQLite ledgers.","Amdavad-online":"An Ahmedabad-inspired browser game at its documented foundation stage.","CSC360-AU2520039":"Java graphics coursework and experiments."}
        description=descriptions.get(name) or repo.get("description") or "Public project"
        meta=" · ".join(x for x in [repo.get("language") or "", f"★ {repo.get('stargazers_count',0)}" if repo.get("stargazers_count",0) else ""] if x)
        lines += [f"**[{clean(name)}]({repo['html_url']})** — {clean(description)}" + (f" <sub>· {clean(meta)}</sub>" if meta else ""),""]
    lines += ["### Recent public activity",""]
    if data["events"]:
        for event in data["events"]:
            lines.append(f"- {event['date']} · {clean(event['label'])} in [{clean(event['repo'])}](https://github.com/{event['repo']})")
    else:
        lines.append("No recent public events are available from GitHub's event feed.")
    lines += ["",f"<sub>Updated {data['date']} UTC · Public GitHub data · See [metric definitions](docs/SETUP.md#metric-definitions).</sub>"]
    return "\n".join(x for x in lines if x is not None).rstrip()+"\n"


def replace_region(readme: str, generated: str) -> str:
    if readme.count(START)!=1 or readme.count(END)!=1 or readme.index(START)>readme.index(END):
        raise ValueError("README must contain one correctly ordered generated region")
    before,rest=readme.split(START,1)
    _,after=rest.split(END,1)
    return before+START+"\n"+generated+END+after


def write_if_changed(path: Path, value: str) -> bool:
    if path.exists() and path.read_text(encoding="utf-8")==value:
        return False
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(value,encoding="utf-8",newline="\n")
    return True


def main() -> int:
    config=json.loads((ROOT/"config/profile.json").read_text(encoding="utf-8"))
    api=GitHub(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "")
    data=collect(api,config)
    outputs={"assets/hero.svg":render_hero(config),"assets/stats.svg":render_stats(data),"assets/languages.svg":render_languages(data),"assets/activity.svg":render_activity(data)}
    for path,value in outputs.items():
        write_if_changed(ROOT/path,value)
    readme=ROOT/"README.md"
    write_if_changed(readme,replace_region(readme.read_text(encoding="utf-8"),generated_markdown(data,config)))
    print(f"Profile generated for {data['user']}; {data['commits_30']} analyzed commits; {api.requests} API requests")
    return 0


if __name__=="__main__":
    try:
        sys.exit(main())
    except (APIError,ValueError,KeyError) as exc:
        print(f"Profile update failed: {exc}",file=sys.stderr)
        sys.exit(1)
