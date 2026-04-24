"""Slice 16 — :class:`_RenameBuffer` pairing + TTL pruning."""
from __future__ import annotations

from donna.memory.sources_vault import _RenameBuffer


def test_match_pops_oldest_and_empties_bucket() -> None:
    buf = _RenameBuffer(ttl_seconds=10.0)
    buf.record_delete("A.md", "h1", now=0.0)
    buf.record_delete("B.md", "h1", now=0.1)  # identical content

    # First add matches the older delete.
    assert buf.match_add("h1", now=0.5) == "A.md"
    # Second add matches the remaining delete.
    assert buf.match_add("h1", now=0.6) == "B.md"
    # Third add has nothing to pair with.
    assert buf.match_add("h1", now=0.7) is None


def test_ttl_prune_drops_stale_entries() -> None:
    buf = _RenameBuffer(ttl_seconds=1.0)
    buf.record_delete("A.md", "h1", now=0.0)

    # Well past the TTL → pruned on match_add.
    assert buf.match_add("h1", now=10.0) is None


def test_discard_removes_specific_entry() -> None:
    buf = _RenameBuffer(ttl_seconds=10.0)
    buf.record_delete("A.md", "h1", now=0.0)
    buf.record_delete("B.md", "h1", now=0.1)

    assert buf.discard("A.md", "h1") is True
    # Only B remains.
    assert buf.match_add("h1", now=0.5) == "B.md"
    assert buf.discard("A.md", "h1") is False


def test_different_hashes_do_not_cross() -> None:
    buf = _RenameBuffer(ttl_seconds=10.0)
    buf.record_delete("A.md", "h1", now=0.0)
    buf.record_delete("B.md", "h2", now=0.0)

    assert buf.match_add("h3", now=0.5) is None
    assert buf.match_add("h1", now=0.5) == "A.md"
    assert buf.match_add("h2", now=0.5) == "B.md"
