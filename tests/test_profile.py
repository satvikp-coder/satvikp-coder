import json
import sys
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from profile import APIError, GitHub, author_matches, collect, personal_commit, replace_region, render_activity, render_hero, select_branches, streaks, write_if_changed


class ProfileTests(unittest.TestCase):
    def test_author_and_automation(self):
        commit = {"author": {"login": "satvikp-coder"}, "commit": {"author": {"email": "other@example.com"}, "message": "Real work"}, "repository": "satvikp-coder/project"}
        self.assertTrue(personal_commit(commit, "satvikp-coder", set(), "satvikp-coder/satvikp-coder"))
        commit["author"] = None
        self.assertFalse(author_matches(commit, "satvikp-coder", set()))
        self.assertTrue(author_matches(commit, "satvikp-coder", {"other@example.com"}))
        commit["repository"] = "satvikp-coder/satvikp-coder"
        commit["commit"]["message"] = "chore(profile): refresh public metrics"
        self.assertFalse(personal_commit(commit, "satvikp-coder", {"other@example.com"}, "satvikp-coder/satvikp-coder"))

    def test_streak_boundaries(self):
        days = [{"date": "2026-10-10", "contributionCount": 2}, {"date": "2026-10-11", "contributionCount": 3}, {"date": "2026-10-12", "contributionCount": 0}, {"date": "2026-10-13", "contributionCount": 0}]
        self.assertEqual(streaks(days, date(2026,10,13)), (0,2))
        days[-2]["contributionCount"] = 1
        self.assertEqual(streaks(days, date(2026,10,13)), (3,3))

    def test_readme_region_preserves_manual_text(self):
        original = "Manual intro\n<!-- PROFILE:GENERATED:START -->\nold\n<!-- PROFILE:GENERATED:END -->\nManual footer\n"
        output = replace_region(original, "new\n")
        self.assertIn("Manual intro", output)
        self.assertIn("Manual footer", output)
        self.assertNotIn("old", output)
        self.assertEqual(output, replace_region(output, "new\n"))

    def test_no_change_write(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            self.assertTrue(write_if_changed(path, "a\n"))
            self.assertFalse(write_if_changed(path, "a\n"))

    def test_svg_escaping(self):
        ET.fromstring(render_hero({"display_name": "A&B <C>"}))
        data = {"commits_7": 1, "commits_30": 2, "added_30": 7, "deleted_30": 3, "calendar": [{"date": "2026-10-13", "contributionCount": 1}], "truncated": False}
        ET.fromstring(render_activity(data))

    def test_empty_pagination(self):
        api = GitHub("test")
        with patch.object(api, "request", return_value=[]):
            self.assertEqual(api.pages("users/test/repos"), [])

    def test_pagination(self):
        api = GitHub("test")
        with patch.object(api, "request", side_effect=[[{}]*100, [{}]]) as request:
            self.assertEqual(len(api.pages("users/test/repos")), 101)
            self.assertEqual(request.call_count, 2)

    def test_full_bounded_event_window(self):
        api = GitHub("test")
        with patch.object(api, "request", side_effect=[[{}]*100, [{}]*100, [{}]*100]) as request:
            self.assertEqual(len(api.pages("users/test/events/public", 3, bounded=True)), 300)
            self.assertEqual(request.call_count, 3)

    def test_branch_selection_prioritizes_default_and_recent_push(self):
        repo = {"full_name": "satvikp-coder/code", "default_branch": "main"}
        event = {"repo": {"name": "satvikp-coder/code"}, "type": "PushEvent", "payload": {"ref": "refs/heads/feature"}}
        selected, capped = select_branches(repo, [{"name": "old"}, {"name": "feature"}], [event], 2)
        self.assertEqual(selected, ["main", "feature"])
        self.assertTrue(capped)

    def test_rate_limit_failure(self):
        api = GitHub("test")
        error = urllib.error.HTTPError("https://api.github.com/x", 403, "forbidden", {"X-RateLimit-Remaining": "0"}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(APIError, "rate limit exhausted"):
                api.request("x")

    def test_collection_counts_diffs_and_excludes_private_event_repo(self):
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        owned = {"name": "code", "full_name": "satvikp-coder/code", "default_branch": "main", "private": False, "fork": False, "size": 4, "pushed_at": "2026-10-13T10:00:00Z", "language": "Python", "stargazers_count": 2}
        event = lambda repo: {"repo": {"name": repo}, "type": "PushEvent", "created_at": "2026-10-13T10:00:00Z", "payload": {}}
        commit = {"sha": "abc", "author": {"login": "satvikp-coder"}, "commit": {"author": {"date": "2026-10-12T10:00:00Z", "email": "a@example.com"}, "message": "feature"}}
        class Fake:
            requests = 0
            def request(self, path, payload=None):
                if path == "user": return {"login": "satvikp-coder"}
                if path == "users/satvikp-coder": return {"login": "satvikp-coder"}
                if path == "repos/satvikp-coder/old-private": return {"private": True, "visibility": "private"}
                if path == "graphql": return {"data": {"user": {"contributionsCollection": {"contributionCalendar": {"totalContributions": 1, "weeks": [{"contributionDays": [{"date": "2026-10-13", "contributionCount": 1}]}]}, "totalCommitContributions": 1, "totalPullRequestContributions": 0}}}}
                if path == "repos/satvikp-coder/code/commits/abc": return {"stats": {"additions": 8, "deletions": 3}, "files": [{"filename": "app.py"}]}
                raise AssertionError(path)
            def pages(self, path, *args, **kwargs):
                if path.startswith("users/satvikp-coder/repos"): return [owned]
                if path.startswith("users/satvikp-coder/events/public"): return [event("satvikp-coder/old-private"), event("satvikp-coder/code")]
                if path.startswith("repos/satvikp-coder/code/branches"): return [{"name": "main"}, {"name": "feature"}]
                if path.startswith("repos/satvikp-coder/code/commits?"): return [commit]
                raise AssertionError(path)
        data = collect(Fake(), {"username": "satvikp-coder", "repository_limit": 20, "commit_detail_limit": 120}, now)
        self.assertEqual((data["commits_7"], data["added_30"], data["deleted_30"]), (1,8,3))
        self.assertEqual(data["changed_file_languages"], [("Python",1)])
        self.assertFalse(any("old-private" in item["repo"] for item in data["events"]))

    def test_detail_cap_prefers_newest_commit_across_repositories(self):
        now = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
        def repo(name, pushed):
            return {"name": name, "full_name": "satvikp-coder/" + name, "default_branch": "main", "private": False, "fork": False, "size": 1, "pushed_at": pushed, "language": "Python", "stargazers_count": 0}
        def commit(sha, date):
            return {"sha": sha, "author": {"login": "satvikp-coder"}, "commit": {"author": {"date": date}, "message": "work"}}
        class Fake:
            requests = 0
            details = []
            def request(self, path, payload=None):
                if path == "user": return {"login": "satvikp-coder"}
                if path == "users/satvikp-coder": return {"login": "satvikp-coder"}
                if path == "graphql": return {"data": {"user": {"contributionsCollection": {"contributionCalendar": {"totalContributions": 2, "weeks": [{"contributionDays": [{"date": "2026-10-13", "contributionCount": 2}]}]}, "totalCommitContributions": 2, "totalPullRequestContributions": 0}}}}
                if "/commits/" in path:
                    self.details.append(path)
                    return {"stats": {"additions": 1, "deletions": 0}, "files": []}
                raise AssertionError(path)
            def pages(self, path, *args, **kwargs):
                if path.startswith("users/satvikp-coder/repos"): return [repo("old", "2026-10-13T11:00:00Z"), repo("new", "2026-10-12T11:00:00Z")]
                if path.startswith("users/satvikp-coder/events/public"): return []
                if path.endswith("/branches"): return [{"name": "main"}]
                if path.startswith("repos/satvikp-coder/old/commits?"): return [commit("oldsha", "2026-10-01T10:00:00Z")]
                if path.startswith("repos/satvikp-coder/new/commits?"): return [commit("newsha", "2026-10-12T10:00:00Z")]
                raise AssertionError(path)
        api = Fake()
        data = collect(api, {"username": "satvikp-coder", "repository_limit": 20, "commit_detail_limit": 1}, now)
        self.assertEqual(api.details, ["repos/satvikp-coder/new/commits/newsha"])
        self.assertEqual(data["commits_30"], 1)
        self.assertTrue(data["truncated"])


if __name__ == "__main__":
    unittest.main()
