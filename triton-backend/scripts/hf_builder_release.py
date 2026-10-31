"""Resolve published HF builder candidates and update the tested default after CI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[2]
VERSION_FILE = Path("triton-backend/app/services/development/hf-builder-version.txt")
README = Path("code-server-extensions/triton-deploy/README.md")
START = "<!-- hf-builder-version:start -->"
END = "<!-- hf-builder-version:end -->"


def stable_version(value: str) -> str:
    parsed = Version(value.strip())
    if parsed.is_prerelease or parsed.is_devrelease:
        raise ValueError("Only stable HF builder releases can be promoted")
    return str(parsed)


def tested_version(root: Path = ROOT) -> str:
    return stable_version((root / VERSION_FILE).read_text(encoding="utf-8"))


def select_candidate(releases: dict[str, list[dict[str, Any]]], requested: str = "") -> str:
    eligible: dict[Version, str] = {}
    for name, files in releases.items():
        try:
            normalized = stable_version(name)
        except (InvalidVersion, ValueError):
            continue
        if any(not entry.get("yanked", False) for entry in files):
            eligible[Version(normalized)] = normalized
    if requested:
        candidate = stable_version(requested)
        if Version(candidate) not in eligible:
            raise ValueError("Requested HF builder release is missing or entirely yanked on PyPI")
        return candidate
    if not eligible:
        raise ValueError("PyPI has no eligible stable HF builder release")
    return eligible[max(eligible)]


def readme_for_version(text: str, version: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise ValueError("README must contain exactly one HF builder version marker pair")
    before, rest = text.split(START)
    _, after = rest.split(END)
    return f"{before}{START}\nTested builder version: `{stable_version(version)}`.\n{END}{after}"


def promote(version: str, root: Path = ROOT) -> None:
    candidate = stable_version(version)
    if Version(candidate) <= Version(tested_version(root)):
        raise ValueError("Candidate must be newer than the tested default")
    # Validate every generated edit before writing either file.
    readme = root / README
    updated = readme_for_version(readme.read_text(encoding="utf-8"), candidate)
    (root / VERSION_FILE).write_text(candidate + "\n", encoding="utf-8")
    readme.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("resolve", "pin", "promote", "check"))
    parser.add_argument("--version", default="")
    args = parser.parse_args()
    if args.command == "pin":
        print(stable_version(args.version) if args.version else tested_version())
    elif args.command == "promote":
        promote(args.version)
    elif args.command == "check":
        readme = (ROOT / README).read_text(encoding="utf-8")
        if readme != readme_for_version(readme, tested_version()):
            raise ValueError("README tested version is stale; update it with the version configuration")
        print("Tested HF builder version and README agree")
    else:
        with urlopen("https://pypi.org/pypi/triton-hf-builder/json", timeout=30) as response:  # nosec B310
            metadata = json.load(response)
        candidate = select_candidate(metadata["releases"], args.version)
        current = tested_version()
        result = {"version": candidate, "current": current,
                  "newer": str(Version(candidate) > Version(current)).lower()}
        if output := os.getenv("GITHUB_OUTPUT"):
            with Path(output).open("a", encoding="utf-8") as stream:
                for key, value in result.items():
                    stream.write(f"{key}={value}\n")
        print(json.dumps(result))


if __name__ == "__main__":
    main()
