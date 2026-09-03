"""Check the exact Git index for common publication mistakes, without printing secrets.

This conservative source-only guardrail is not an exhaustive secret scanner or
application security audit. It intentionally does not inspect ignored local data.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


MAX_BLOB_BYTES = 1024 * 1024
PRIVATE_PARTS = frozenset({
    ".agent-docs", ".codex", ".agents", ".venv", "__pycache__", "build", "dist",
    "runtime", "components", "prefixes", "jobs", "cache", "runtime-snapshots",
    "prepared-clips", "tmp", "test-output", "runtime-data",
})
BINARY_SUFFIXES = frozenset({
    ".dll", ".exe", ".addon64", ".so", ".dylib", ".pdb", ".zip", ".7z",
    ".mp4", ".mkv", ".mov", ".rgba", ".rg16f", ".pem", ".key", ".p12", ".pfx",
    ".pyc", ".log",
})
RULES = {
    "private-key": re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    "github-token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "api-key": re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{24,}|AIzaSy[A-Za-z0-9_-]{25,})\b"),
    "aws-access-key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private-host-address": re.compile(
        r"(?<![\w.])(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})(?![\w.])"
    ),
    "personal-home-path": re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+/|[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.-]+[\\/]"),
    "url-credentials": re.compile(r"https?://[^\s/:\"']+:[^\s/@\"']+@[^\s/\"'<>]+"),
}
# Deliberate invalid-URL unit fixture, not a real credential. No broad path skips.
SAFE_URL_FIXTURE = "https://user:pass@example.com"


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str


def inspect_blob(path: str, data: bytes, mode: str = "100644") -> list[Finding]:
    findings: list[Finding] = []
    parts = PurePosixPath(path).parts
    filename = PurePosixPath(path).name
    if mode not in {"100644", "100755"}:
        findings.append(Finding(path, 0, "non-regular-source-file"))
    if (PRIVATE_PARTS.intersection(parts) or parts[:2] == ("sidecar", "bin")
            or (filename.startswith(".env") and filename != ".env.example")
            or filename == ".DS_Store"):
        findings.append(Finding(path, 0, "private-or-generated-path"))
    if PurePosixPath(path).suffix.lower() in BINARY_SUFFIXES or ".so." in filename:
        findings.append(Finding(path, 0, "binary-or-private-file-type"))
    if len(data) > MAX_BLOB_BYTES:
        return findings + [Finding(path, 0, "oversized-source-blob")]
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        return findings + [Finding(path, 0, "non-text-blob")]
    if "\x00" in content:
        return findings + [Finding(path, 0, "non-text-blob")]
    for number, line in enumerate(content.splitlines(), 1):
        for rule, pattern in RULES.items():
            for match in pattern.finditer(line):
                if rule == "url-credentials" and match.group(0) == SAFE_URL_FIXTURE:
                    continue
                findings.append(Finding(path, number, rule))
                break
    return findings


def git(root: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def inspect_index(root: Path) -> tuple[int, int, list[Finding]]:
    entries = git(root, "ls-files", "--stage", "-z").split(b"\x00")
    count, total = 0, 0
    findings: list[Finding] = []
    for entry in filter(None, entries):
        metadata, raw_path = entry.split(b"\t", 1)
        mode, object_id, stage = metadata.decode("ascii").split()
        path = raw_path.decode("utf-8", errors="backslashreplace")
        count += 1
        if stage != "0" or mode not in {"100644", "100755"}:
            findings.append(Finding(path, 0, "unmerged-or-non-regular-entry"))
            continue
        size = int(git(root, "cat-file", "-s", object_id))
        total += size
        if size > MAX_BLOB_BYTES:
            findings.append(Finding(path, 0, "oversized-source-blob"))
            continue
        findings.extend(inspect_blob(path, git(root, "cat-file", "blob", object_id), mode))
    if not count:
        findings.append(Finding("<index>", 0, "empty-index-stage-source-first"))
    return count, total, findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        count, total, findings = inspect_index(args.repo)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("Could not read Git index; check repository access and index state.", file=sys.stderr)
        return 2
    for finding in findings:
        print(f"{finding.path!r}:{finding.line}: {finding.rule}")
    print(f"Checked {count} staged files ({total} bytes); {len(findings)} findings.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
