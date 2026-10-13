# CYBER // MIDNIGHT maintenance

This public profile repository is named exactly like its owner, so GitHub renders `README.md` on the profile page. Python 3.12 and the standard library are the only runtime requirements. GitHub Actions runs the generator with its repository-scoped `GITHUB_TOKEN`.

## Refresh behavior

The workflow runs at minute 17 every six hours in UTC, on manual `workflow_dispatch`, on selected profile repository pushes, and on optional `repository_dispatch` events of type `refresh-profile`. Change the cron expression in `.github/workflows/update-profile.yml` to adjust frequency. GitHub can delay scheduled jobs, and public-repository schedules may be disabled after 60 days without repository activity. If updates stop, inspect the Actions tab, re-enable the workflow there, and run it manually. A generated commit is made only when output changes. GitHub's `GITHUB_TOKEN` commits do not recursively trigger the push workflow.

GitHub's native contribution calendar is maintained by GitHub and has its own eligibility and update timing. The custom cards are snapshots from the last successful workflow generation, normally refreshed within six hours plus scheduling delay. The `UPDATED` card gives the UTC date of the last successful data collection; the date granularity avoids unnecessary commits on identical runs.

## Metric definitions

| Metric | Scope |
| --- | --- |
| Public repos | Public repositories returned by the owner's REST repository list, including forks. |
| Repo stars | Current stars on public non-fork repositories owned by the account. |
| Contributions | GraphQL visible contribution calendar total for the previous rolling 365 days. Includes eligible commits and other contribution types. |
| Commit / PR contributions | GitHub GraphQL contribution totals for the same rolling 365-day interval. They are separate GitHub contribution measures, not the analyzed commit sample. |
| Current / longest streak | Consecutive days with at least one visible contribution within the returned calendar; current may start yesterday if today is zero. Longest is limited to the 365-day calendar. |
| 7d / 30d commits | Matched authored public commits, analyzed from public owned non-fork repositories and recent public event repositories, capped by `repository_limit`, `commit_list_limit_per_repository`, and `commit_detail_limit`. Counts are a bounded analyzed sample, not a lifetime total. |
| Lines added / deleted | GitHub per-commit diff statistics summed over analyzed 30-day commits. These are changed lines, not original lines of code authored. Merge or generated-file diffs may affect them. |
| Repository languages | Number of public owned non-fork repositories whose GitHub primary language is that language. This is a repository count, not proficiency or byte percentage. |
| Recent changed-file languages | Count of source files touched in analyzed commits, classified by extension. Each touch is counted; this is not time spent or lines by language. |
| Recent public activity | Deduplicated public Events API entries. The public event feed is limited by GitHub's retention and may miss some work. |

The generator uses REST author filtering by GitHub username, then verifies linked `author.login` on each commit. This catches GitHub-associated author identities. To include an unlinked but verified commit email, explicitly add it to `author_emails` in `config/profile.json`; use only an address you are comfortable storing publicly. Display-name-only matching is deliberately excluded. Bot identities and generated profile commits are excluded. The script fails on missing API statistics instead of writing zeros. Deleted or empty repositories referenced by old public events are skipped.

Pagination is bounded to keep public Actions runs practical. The default analyzes at most 20 repositories, 100 listed commits per repository, and 120 commit details in a rolling 30-day window. The activity card shows `SAMPLE CAPPED` if a cap is reached. It does not claim complete activity beyond those bounds. GitHub Events data is queried for external repositories with recent public events; public contributions outside that event horizon may not be in the commit sample.

## Customize

Edit `config/profile.json` to change the six featured repository names, optional contact URLs, and collection limits. Featured entries appear only when the named public owned repository exists. Manually edit text outside the generated README markers; the generator replaces only the marked region. The `Currently exploring` section is intentionally manual. If you configure LinkedIn, portfolio, or public email, add its link manually in the footer; the generator never reads local Git identity or publishes the GitHub account email.

For a local refresh, set `GITHUB_TOKEN` or `GH_TOKEN` to an authenticated token, run `python scripts/profile.py`, then `python -m unittest discover -s tests -v` and `python scripts/validate.py`. Do not print or commit the token.

## Optional faster cross-repository refresh

The default schedule needs no setup in other repositories. For near-immediate updates after pushes elsewhere, use a GitHub App installation token or fine-grained token limited to the profile repository with **Actions: write** permission. Store it as a secret named `PROFILE_REFRESH_TOKEN` in the source repository. Add this workflow there, changing the branch to its default branch:

```yaml
name: Refresh profile after push
on:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - name: Dispatch profile workflow
        env:
          PROFILE_REFRESH_TOKEN: ${{ secrets.PROFILE_REFRESH_TOKEN }}
        run: |
          curl --fail-with-body --silent --show-error -X POST \
            -H "Authorization: Bearer $PROFILE_REFRESH_TOKEN" \
            -H "Accept: application/vnd.github+json" \
            https://api.github.com/repos/satvikp-coder/satvikp-coder/actions/workflows/update-profile.yml/dispatches \
            -d '{"ref":"main"}'
```

The source repository's default `GITHUB_TOKEN` cannot access the profile repository. This method uses the workflow dispatch API, which requires Actions write on the target repository. The profile workflow also accepts `repository_dispatch` with event type `refresh-profile`; that alternative requires **Contents: write** on the target repository and is therefore less narrowly scoped for this purpose. Configure only repositories where faster refresh is valuable; the scheduled refresh remains the fallback. Revoke the credential to disable this optional path. No cross-repository trigger is installed by default.

GitHub API references: [REST commits](https://docs.github.com/en/rest/commits/commits), [workflow schedule](https://docs.github.com/en/actions/reference/events-that-trigger-workflows#schedule), [GITHUB_TOKEN](https://docs.github.com/en/actions/security-for-github-actions/security-guides/automatic-token-authentication), [workflow dispatch](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event), [repository dispatch](https://docs.github.com/en/rest/repos/repos#create-a-repository-dispatch-event).
