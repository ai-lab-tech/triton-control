#!/usr/bin/env bash
set -euo pipefail

version=8.30.1
sha256=551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

curl --fail --silent --show-error --location --retry 4 --retry-all-errors --connect-timeout 10 --max-time 120 \
  "https://github.com/gitleaks/gitleaks/releases/download/v${version}/gitleaks_${version}_linux_x64.tar.gz" \
  --output "$tmp/gitleaks.tar.gz"
printf '%s  %s\n' "$sha256" "$tmp/gitleaks.tar.gz" | sha256sum --check --status
tar -xzf "$tmp/gitleaks.tar.gz" -C "$tmp" gitleaks
"$tmp/gitleaks" dir --redact --verbose .
