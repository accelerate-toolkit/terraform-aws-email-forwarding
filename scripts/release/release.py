# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Plan and publish immutable Terraform module source releases."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess


VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SUBJECT = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?(?P<breaking>!)?: .+")
RELEASE = re.compile(r"^chore\(release\): v(\d+\.\d+\.\d+)$")


def run(*args: str) -> str:
    return subprocess.check_output(args, text=True).rstrip("\r\n")


def git(*args: str) -> str:
    return run("git", *args)


def version_tuple(value: str) -> tuple[int, ...]:
    if not VERSION.fullmatch(value):
        raise ValueError(f"invalid version: {value}")
    return tuple(map(int, value.split(".")))


def commits(start: str | None, end: str = "HEAD") -> list[dict]:
    raw = git("log", "--format=%H%x1f%s%x1f%b%x1e", f"{start}..{end}" if start else end)
    return [dict(zip(("sha", "subject", "body"), record.strip("\r\n").split("\x1f", 2)))
            for record in raw.split("\x1e") if record.strip("\r\n")]


def bump_type(changes: list[dict]) -> str | None:
    bump = None
    for change in changes:
        match = SUBJECT.match(change["subject"])
        if (match and match["breaking"]) or re.search(r"(?m)^BREAKING[ -]CHANGE: .+", change["body"]):
            return "major"
        if match and match["type"] == "feat":
            bump = "minor"
        elif match and match["type"] in {"fix", "perf"} and bump != "minor":
            bump = "patch"
    return bump


def notes(version: str, changes: list[dict]) -> str:
    lines = [f"# v{version}", ""]
    for change in reversed(changes):
        if RELEASE.fullmatch(change["subject"]):
            continue
        lines.append(f"- {change['subject']} ({change['sha'][:7]})")
        if change["body"].strip():
            lines.extend(["", *[f"  {line}" for line in change["body"].strip().splitlines()], ""])
    return "\n".join(lines) + "\n"



def tags():
    return sorted((tag for tag in git("tag", "--merged", "HEAD", "--list", "v*").splitlines()
                   if VERSION.fullmatch(tag[1:])), key=lambda tag: version_tuple(tag[1:]))


def plan(repo=None):
    releases = tags()
    latest = releases[-1] if releases else None
    if latest and repo:
        pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"))
        if not any(r["tag_name"] == latest and not r["draft"] for page in pages for r in page):
            # Resume tag/draft publication before considering newer commits.
            previous = releases[-2] if len(releases) > 1 else None
            commit = git("rev-parse", latest + "^{commit}")
            return dict(version=latest[1:], commit=commit,
                        notes=notes(latest[1:], commits(previous, commit)))
    changes = commits(latest)
    bump = bump_type(changes)
    if not bump:
        return None
    major, minor, patch = version_tuple(latest[1:]) if latest else (0, 0, 0)
    version = (major + 1, 0, 0) if bump == "major" else ((major, minor + 1, 0) if bump == "minor" else (major, minor, patch + 1))
    version = ".".join(map(str, version))
    return dict(version=version, commit=git("rev-parse", "HEAD"), notes=notes(version, changes))


def gh(*args: str) -> str:
    return run("gh", *args)


def publish(repo: str, version: str, commit: str, notes_path: Path) -> None:
    version_tuple(version)
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("repository and full commit SHA are required")
    git("merge-base", "--is-ancestor", commit, "HEAD")
    if not notes_path.is_file():
        raise ValueError("release notes are missing")
    tag = "v" + version
    refs = json.loads(gh("api", f"repos/{repo}/git/matching-refs/tags/{tag}"))
    if any(ref["ref"] == f"refs/tags/{tag}" for ref in refs):
        actual = json.loads(gh("api", f"repos/{repo}/commits/{tag}"))["sha"]
        if actual != commit:
            raise ValueError(f"refusing to move published tag {tag}")
    else:
        gh("api", "--method", "POST", f"repos/{repo}/git/refs", "-f", f"ref=refs/tags/{tag}", "-f", f"sha={commit}")
    pages = json.loads(gh("api", "--paginate", "--slurp", f"repos/{repo}/releases?per_page=100"))
    release = next((item for page in pages for item in page if item["tag_name"] == tag), None)
    if release and not release["draft"]:
        print(f"{tag} is already published; left unchanged")
        return
    if not release:
        gh("release", "create", tag, "--repo", repo, "--verify-tag", "--draft",
           "--title", tag, "--notes-file", str(notes_path))
    gh("release", "edit", tag, "--repo", repo, "--notes-file", str(notes_path), "--draft=false", "--latest")



def output_version(version):
    version_tuple(version)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"version={version}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "publish"])
    parser.add_argument("--repo", help="OWNER/REPO; required for CI publication")
    args = parser.parse_args()
    if args.action == "publish":
        if os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("GITHUB_REF") != "refs/heads/main":
            parser.error("publication runs only in the checked main-branch GitHub job")
        if not args.repo or not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo):
            parser.error("a valid --repo is required")
        if git("status", "--porcelain"):
            parser.error("publication requires a clean checkout")
    result = plan(args.repo if args.action == "publish" else None)
    if not result:
        # Retry post-publication verification even when no new release is due.
        if args.action == "publish" and tags():
            output_version(tags()[-1][1:])
        print("No release-eligible changes.")
        return
    path = Path("build/release/notes.md")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(result["notes"])
    print(json.dumps({k: v for k, v in result.items() if k != "notes"}))
    if args.action == "publish":
        publish(args.repo, result["version"], result["commit"], path)
        output_version(result["version"])


if __name__ == "__main__":
    main()
