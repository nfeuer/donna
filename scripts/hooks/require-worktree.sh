#!/bin/bash
# PreToolUse hook for Edit|Write — blocks edits on main branch in the main
# worktree to enforce isolation when multiple Claude instances run concurrently.
#
# Exempt: CLAUDE.md, .claude/settings*, config/, .gitignore

file="$CLAUDE_FILE_PATH"
[[ -z "$file" ]] && exit 0

dir="$(dirname "$file")"
[[ ! -d "$dir" ]] && exit 0

# --- Exemptions: config files that are safe to edit on main ---
base="$(basename "$file")"
case "$file" in
    */CLAUDE.md|*/.claude/settings*|*/config/*.yaml|*/config/*.yml|*/.gitignore)
        exit 0 ;;
esac

# --- Must be in a git repo on main/master ---
branch=$(git -C "$dir" branch --show-current 2>/dev/null) || exit 0
[[ "$branch" != "main" && "$branch" != "master" ]] && exit 0

# --- Already in a linked worktree? Allow. ---
git_dir=$(cd "$dir" && cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd -P)
git_common=$(cd "$dir" && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
[[ -z "$git_dir" || -z "$git_common" ]] && exit 0

if [[ "$git_dir" != "$git_common" ]]; then
    # Might be a submodule — check before allowing
    superproject=$(git -C "$dir" rev-parse --show-superproject-working-tree 2>/dev/null)
    [[ -z "$superproject" ]] && exit 0
fi

# --- On main in main worktree — block ---
repo_root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null)
cat >&2 <<EOF
BLOCKED: Editing source files on main is disabled to prevent conflicts between concurrent sessions.

Create an isolated worktree before editing:
  git worktree add ${repo_root}/.claude/worktrees/<name> -b fix/<name>

Then use absolute paths under that worktree for all subsequent edits.
When finished, commit, push, and create a PR from the branch.

Exempt files (editable on main): CLAUDE.md, .claude/settings*, config/*.yaml, .gitignore
EOF
exit 2
