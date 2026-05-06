"""Scope-check a manually-built branch against the spec's target_paths.

When the user clicks **Mark as built** for a claude_code escalation,
the poller diffs ``base..tip`` and checks every touched path against
the row's snapshotted ``target_paths`` globs (with ``{name}`` already
substituted at gate-fire time — see
:class:`donna.cost.claude_code_spec.ClaudeCodeSpecBuilder`).

Out-of-scope files force the row into ``status='failed'`` with a
``validation_result`` blob naming the offending paths so the user can
fix-and-resubmit. Iteration cap from
:func:`donna.cost.escalation_submit.MANUAL_ITERATION_LIMIT` applies.

Realizes docs/superpowers/specs/manual-escalation.md §10.3 row 3.
"""

from __future__ import annotations

import dataclasses
import fnmatch
import re

import structlog

logger = structlog.get_logger()


# Spec §10.3 row 3 + brainstorm decision: dotfile additions are always
# rejected because they're never in the legitimate scope of a skill
# build (config files, CI, IDE settings, secrets, etc.). Matches any
# path component starting with ``.``.
_DOTFILE_RE = re.compile(r"(^|/)\.[^/]+")


@dataclasses.dataclass(frozen=True)
class DiffValidatorResult:
    """Outcome of a scope check."""

    matched: list[str]
    """Paths whose change is within the declared scope."""

    out_of_scope: list[str]
    """Paths NOT covered by any glob — these reject the submission."""

    @property
    def ok(self) -> bool:
        return not self.out_of_scope


class DiffValidator:
    """Validates that a branch's diff is contained by the row's globs.

    Stateless. Construct once, call :meth:`validate` per submission.
    """

    @staticmethod
    def validate(
        diff_paths: list[str],
        target_paths: dict[str, str],
    ) -> DiffValidatorResult:
        """Partition ``diff_paths`` into matched / out-of-scope.

        A path is **matched** iff it matches at least one glob in
        ``target_paths.values()`` AND is not a dotfile addition. Globs
        are evaluated with :func:`fnmatch.fnmatchcase` (case-sensitive)
        — this is what spec §6.2 implies (paths under ``src/donna/``
        are case-sensitive on every supported filesystem).

        Globs ending in ``/**`` are treated as recursive prefixes — the
        path matches if it begins with the literal portion (e.g.
        ``skills/foo/**`` matches ``skills/foo/skill.yaml`` AND
        ``skills/foo/steps/extract.md``). This is the interpretation
        used by ``rsync``, ``git pathspec`` etc. and matches how
        ``task_types.yaml`` declares skill scope.

        ``target_paths`` is expected to be the **already substituted**
        glob dict — the gate snapshots the rendered globs onto the row
        at fire time so the validator never has to run substitution.

        Args:
            diff_paths: Paths from ``git diff --name-only base..tip``.
            target_paths: ``{label: glob}`` dict from the row.

        Returns:
            :class:`DiffValidatorResult`.
        """
        if not target_paths:
            # No declared scope — everything is out-of-scope. Defensive:
            # the row should always carry target_paths for claude_code
            # mode, but if it doesn't, fail closed.
            return DiffValidatorResult(matched=[], out_of_scope=list(diff_paths))

        globs = list(target_paths.values())
        matched: list[str] = []
        out_of_scope: list[str] = []
        for path in diff_paths:
            if _DOTFILE_RE.search(path):
                out_of_scope.append(path)
                continue
            if any(_match(path, g) for g in globs):
                matched.append(path)
            else:
                out_of_scope.append(path)

        if out_of_scope:
            logger.info(
                "diff_validator_rejected",
                out_of_scope=out_of_scope,
                matched=matched,
                globs=globs,
            )
        return DiffValidatorResult(matched=matched, out_of_scope=out_of_scope)


def _match(path: str, glob: str) -> bool:
    """Match ``path`` against ``glob`` with ``/**`` recursion support.

    The default :func:`fnmatch.fnmatchcase` doesn't recurse across
    ``/`` separators, but ``glob`` patterns ending in ``/**`` are the
    natural way to express "any path under this directory". We
    interpret ``foo/bar/**`` as a literal prefix check (path starts
    with ``foo/bar/``); other globs fall through to fnmatch.
    """
    if glob.endswith("/**"):
        prefix = glob[:-2]  # keeps the trailing slash
        return path.startswith(prefix)
    return fnmatch.fnmatchcase(path, glob)
