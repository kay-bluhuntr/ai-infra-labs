#!/usr/bin/env bash
# Points every Argo CD manifest at YOUR GitHub repo.
# Usage:  ./scripts/set-repo.sh <your-github-username>
set -euo pipefail
user="${1:?usage: ./scripts/set-repo.sh <your-github-username>}"
cd "$(dirname "$0")/.."
files=$(grep -rl "YOUR_GITHUB_USER" bootstrap platform labs/apps)
perl -pi -e "s/YOUR_GITHUB_USER/${user}/g" $files
echo "Updated:"; echo "$files" | sed 's/^/  /'
grep -rh "repoURL: https://github.com" bootstrap platform labs/apps | sort -u
