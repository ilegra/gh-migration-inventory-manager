"""Utility script to generate release notes for CI/CD pipelines."""

from __future__ import annotations

import argparse
import shutil
import subprocess  # nosec B404


def _get_git_binary() -> str:
    """Resolve full path to git executable."""
    git_bin = shutil.which("git")
    return git_bin if git_bin else "/usr/bin/git"


def get_commit_log() -> list[str]:
    """Retrieve commits since the previous git tag or initial commit."""
    git_bin = _get_git_binary()
    try:
        latest_tag = (
            subprocess.check_output(  # nosec B603
                [git_bin, "describe", "--tags", "--abbrev=0"],
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
        range_spec = f"{latest_tag}..HEAD"
    except subprocess.CalledProcessError:
        range_spec = "HEAD"

    try:
        log_output = (
            subprocess.check_output(  # nosec B603
                [git_bin, "log", range_spec, "--pretty=format:%s"],
                stderr=subprocess.DEVNULL,
            )
            .decode("utf-8")
            .strip()
        )
        if not log_output:
            return []
        return log_output.splitlines()
    except subprocess.CalledProcessError:
        return []


def generate_notes(commits: list[str]) -> str:
    """Categorize commit messages into standard release categories."""
    features: list[str] = []
    fixes: list[str] = []
    maintenance: list[str] = []
    others: list[str] = []

    for commit in commits:
        if commit.startswith("feat"):
            features.append(commit)
        elif commit.startswith("fix"):
            fixes.append(commit)
        elif commit.startswith(("chore", "ci", "docs", "style", "refactor")):
            maintenance.append(commit)
        else:
            others.append(commit)

    sections: list[str] = ["# Release Notes\n"]

    if features:
        sections.append("### 🚀 Features")
        sections.extend(f"- {c}" for c in features)
        sections.append("")

    if fixes:
        sections.append("### 🐛 Bug Fixes")
        sections.extend(f"- {c}" for c in fixes)
        sections.append("")

    if maintenance:
        sections.append("### 🧰 Maintenance & CI")
        sections.extend(f"- {c}" for c in maintenance)
        sections.append("")

    if others:
        sections.append("### 📦 Other Changes")
        sections.extend(f"- {c}" for c in others)
        sections.append("")

    if len(sections) == 1:
        sections.append("Maintenance and library updates.")

    return "\n".join(sections)


def main() -> None:
    """CLI entrypoint for release notes generation."""
    parser = argparse.ArgumentParser(description="Generate release notes from git log.")
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="RELEASE_NOTES.md",
        help="Path to output markdown file",
    )
    args = parser.parse_args()

    commits = get_commit_log()
    notes = generate_notes(commits)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(notes + "\n")

    print(f"Release notes written to {args.output}")


if __name__ == "__main__":
    main()
