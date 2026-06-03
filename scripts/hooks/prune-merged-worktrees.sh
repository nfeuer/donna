#!/bin/bash
# Removes worktrees under .claude/worktrees/ whose PR has been merged on GitHub.
#
# Safety rules — a worktree is pruned ONLY when ALL hold:
#   - it lives under .claude/worktrees/
#   - it is not locked (locked == active background agent)
#   - it has no uncommitted changes
#   - it has had no file/git activity in the last RECENCY_MIN minutes
#     (guards a merged+clean worktree another live session is using)
#   - it is not the current working directory
#   - it has an open PR for its branch: none; and a merged PR for its branch:
#     yes (asked of GitHub directly, since squash merges and
#     deleteBranchOnMerge=false defeat local detection)
#
# Usage: prune-merged-worktrees.sh         (prune everything eligible)
#        prune-merged-worktrees.sh <branch> (consider only this branch)
# Env:   DONNA_PRUNE_RECENCY_MIN  recency threshold in minutes (default 30; 0 disables)

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
WT_PREFIX="$REPO_ROOT/.claude/worktrees/"
ONLY_BRANCH="${1:-}"
CWD=$(pwd -P)
RECENCY_MIN="${DONNA_PRUNE_RECENCY_MIN:-30}"

command -v gh >/dev/null 2>&1 || exit 0
gh auth status >/dev/null 2>&1 || exit 0

# True if the worktree had file or git activity within RECENCY_MIN minutes.
recently_active() {
    local wt="$1"
    [[ "$RECENCY_MIN" -le 0 ]] && return 1
    # Any working file modified recently (skip vendored/build/cache trees).
    if find "$wt" \
        \( -name .git -o -name node_modules -o -name __pycache__ -o -name .venv \) -prune -o \
        -type f -mmin "-$RECENCY_MIN" -print -quit 2>/dev/null | grep -q .; then
        return 0
    fi
    # Recent git activity (checkout/commit) in this worktree's gitdir.
    local gd f
    gd=$(git -C "$wt" rev-parse --git-dir 2>/dev/null) || return 1
    for f in HEAD index logs/HEAD; do
        [[ -e "$gd/$f" ]] && find "$gd/$f" -mmin "-$RECENCY_MIN" -print -quit 2>/dev/null | grep -q . && return 0
    done
    return 1
}

# Parse `git worktree list --porcelain` into path|branch|locked records.
path="" branch="" locked=0
flush() {
    [[ -z "$path" ]] && return
    process "$path" "$branch" "$locked"
    path="" branch="" locked=0
}

process() {
    local wt="$1" br="$2" lk="$3"
    [[ "$wt" != "$WT_PREFIX"* ]] && return            # only our worktrees
    [[ "$lk" == "1" ]] && return                       # locked = active agent
    [[ -z "$br" ]] && return                           # detached HEAD, leave it
    [[ -n "$ONLY_BRANCH" && "$br" != "$ONLY_BRANCH" ]] && return

    if [[ -n "$(git -C "$wt" status --porcelain 2>/dev/null)" ]]; then
        echo "skip: $br has uncommitted changes" >&2
        return
    fi

    if recently_active "$wt"; then
        echo "skip: $br active within ${RECENCY_MIN}m — leaving for the live session" >&2
        return
    fi

    # Keep if an open PR exists for this branch head — guards against branch-name
    # reuse, where a stale merged PR shares a name with active work.
    local open_prs merged_prs
    open_prs=$(gh pr list --head "$br" --state open --json number 2>/dev/null) || return
    [[ "$open_prs" != "[]" ]] && return
    # Prune only when a merged PR exists for this exact branch head.
    merged_prs=$(gh pr list --head "$br" --state merged --json number 2>/dev/null) || return
    [[ "$merged_prs" == "[]" ]] && return

    if [[ "$wt" == "$CWD" ]]; then
        echo "note: $br is merged but is your current directory — it will be pruned on next session" >&2
        return
    fi

    if git worktree remove "$wt" 2>/dev/null; then
        git branch -D "$br" >/dev/null 2>&1
        echo "pruned merged worktree: $br ($wt)" >&2
    else
        echo "skip: could not remove $wt" >&2
    fi
}

while IFS= read -r line; do
    case "$line" in
        "worktree "*) flush; path="${line#worktree }" ;;
        "branch refs/heads/"*) branch="${line#branch refs/heads/}" ;;
        "locked"*) locked=1 ;;
        "") flush ;;
    esac
done < <(git worktree list --porcelain)
flush

git worktree prune >/dev/null 2>&1
exit 0
