#!/usr/bin/env bash
# Plant each change in changes/ on a branch of its own off a pinned humanize
# commit, index the pinned commit, and analyse every branch against it.
#
# Answers are recorded in answers/ and committed, so a second run replays them
# and spends nothing. The OpenAI client wants a key to exist even when every
# answer is recorded; any value does for a replay:
#
#     OPENAI_API_KEY=unused validation/humanize/run.sh
#
# results/ holds what the paying run printed. A replay prints the same findings
# and repairs, with the last line reporting nothing spent.
#
# A change with no recorded answer is asked for real, at most $0.03 per change,
# so the eleven cannot pass the $0.30 set aside for this run.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
synclint=$(cd "$here/../.." && pwd)
# Pinned so the patches keep applying and the answers keep matching their
# prompts; humanize's main moves every week.
upstream=https://github.com/python-humanize/humanize.git
base=392aef707c0e74341ab4a51420984e9ea6b566c5
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

git clone --quiet "$upstream" "$work/humanize"
git -C "$work/humanize" checkout --quiet "$base"
git -C "$work/humanize" branch --quiet base

cd "$synclint"
uv run python -m synclint index "$work/humanize" --out "$work/index.json"

mkdir -p "$here/results"
for patch in "$here"/changes/*.patch; do
    name=$(basename "$patch" .patch)
    git -C "$work/humanize" checkout --quiet -b "$name" base
    git -C "$work/humanize" apply "$patch"
    git -C "$work/humanize" -c commit.gpgsign=false \
        -c user.name=synclint -c user.email=synclint@localhost \
        commit --quiet -am "$name"
    git -C "$work/humanize" checkout --quiet base
    # analyse exits 1 when it stops at the ceiling; record that and go on.
    uv run python -m synclint analyse "$work/humanize" \
        --base base --head "$name" --index "$work/index.json" \
        --cache "$here/answers" --ceiling 0.03 \
        > "$here/results/$name.txt" || echo "exit $?" >> "$here/results/$name.txt"
    echo "$name: $(tail -1 "$here/results/$name.txt")"
done
