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
# results/ holds what the paying run printed, and index.json the index it ran
# against. Two files have since gained a finding that cost nothing: a
# disappearance (#10), where the chunk a section names was deleted. Their
# spend lines are still the paying run's. A replay writes elsewhere and is compared with results/, the last
# line of each aside, since that is the one that says what was spent.
#
# A change with no recorded answer is asked for real, at most $0.03 per change.
# Nine of the eleven reach the model, so a full recording cannot pass $0.27,
# inside the $0.30 set aside for it.
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
uv run python -m synclint index "$work/humanize" --out "$here/index.json"

mkdir -p "$work/results"
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
        --base base --head "$name" --index "$here/index.json" \
        --cache "$here/answers" --ceiling 0.03 \
        > "$work/results/$name.txt" || echo "exit $?" >> "$work/results/$name.txt"
    echo "$name: $(tail -1 "$work/results/$name.txt")"
done

if [ -d "$here/results" ]; then
    # The Index: line, and the blank after it, are ignored too: results/
    # predates #11, which added them.
    diff -r -I 'spent\.$' -I '^Index: ' -I '^$' "$here/results" "$work/results"
    echo "Findings and repairs match the recorded run."
else
    cp -r "$work/results" "$here/results"
fi
