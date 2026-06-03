#!/bin/bash
# PostToolUse(Bash) hook — after a `gh pr merge`, sweep merged worktrees.
# Receives the tool-call JSON on stdin; only acts on gh-pr-merge commands.

input=$(cat 2>/dev/null)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
printf '%s' "$cmd" | grep -Eq 'gh[[:space:]]+pr[[:space:]]+merge' || exit 0

bash "$(dirname "$0")/prune-merged-worktrees.sh"
exit 0
