"""Unit tests for :class:`MarkdownHeadingChunker`."""
from __future__ import annotations

import pytest

from donna.memory.chunking import MarkdownHeadingChunker, count_tokens


def test_heading_stack_propagates_three_levels() -> None:
    # Both sections are large enough to avoid the merge-forward path,
    # so each heading_path appears on its own chunk.
    intro = " ".join(f"intro{i}" for i in range(60))
    detail = " ".join(f"detail{i}" for i in range(60))
    body = (
        f"# Top\n\n{intro}\n\n"
        f"## Section A\n\n### Subsection\n\n{detail}\n"
    )
    chunks = MarkdownHeadingChunker(
        max_tokens=120, overlap_tokens=8, min_tokens=10,
    ).chunk(body)
    paths = [c.heading_path for c in chunks]
    assert ["Top", "", ""] in paths
    assert ["Top", "Section A", "Subsection"] in paths


def test_respects_max_tokens() -> None:
    para = "word " * 400
    body = f"# Title\n\n{para}\n"
    chunker = MarkdownHeadingChunker(max_tokens=50, overlap_tokens=5)
    chunks = chunker.chunk(body)
    assert len(chunks) > 1
    for c in chunks:
        # Allow a small slack for overlap-seeded chunks.
        assert c.token_count <= 55, f"chunk tok={c.token_count}"


def test_overlap_seeds_next_chunk() -> None:
    body = "# T\n\n" + " ".join(f"alpha{i}" for i in range(200)) + "\n"
    chunker = MarkdownHeadingChunker(max_tokens=40, overlap_tokens=8)
    chunks = chunker.chunk(body)
    assert len(chunks) >= 2
    # The tail of chunk N should appear near the head of chunk N+1.
    from itertools import pairwise

    for a, b in pairwise(chunks):
        tail = a.content[-40:]
        assert any(word in b.content for word in tail.split() if word.strip())


def test_code_fence_stays_intact_when_it_fits() -> None:
    fence = "```python\n" + "x = 1\n" * 3 + "```"
    body = f"# Code\n\nIntro line.\n\n{fence}\n"
    chunker = MarkdownHeadingChunker(max_tokens=120, overlap_tokens=8)
    chunks = chunker.chunk(body)
    fenced = [c for c in chunks if "```python" in c.content]
    assert len(fenced) == 1
    # The closing fence must be in the same chunk as the opener.
    assert fenced[0].content.count("```") == 2


def test_short_section_merges_forward() -> None:
    body = (
        "# Top\n\n"
        + "Long paragraph. " * 20
        + "\n\n## Tiny\n\nshort.\n"
    )
    chunker = MarkdownHeadingChunker(max_tokens=120, overlap_tokens=4, min_tokens=20)
    chunks = chunker.chunk(body)
    # The "short." content should not get its own chunk when the
    # prior chunk has spare capacity.
    isolated = [c for c in chunks if c.content.strip() == "short."]
    assert not isolated


def test_oversized_atom_is_window_split() -> None:
    # A single "paragraph" with 400 words — the chunker normally splits
    # on paragraph boundaries, but here there's only one atom, so the
    # window-split fallback must kick in.
    atom = " ".join(f"word{i}" for i in range(400))
    body = f"# T\n\n{atom}\n"
    chunker = MarkdownHeadingChunker(max_tokens=60, overlap_tokens=5)
    chunks = chunker.chunk(body)
    assert len(chunks) >= 3


def test_invalid_config_rejected() -> None:
    with pytest.raises(ValueError):
        MarkdownHeadingChunker(max_tokens=0)
    with pytest.raises(ValueError):
        MarkdownHeadingChunker(max_tokens=10, overlap_tokens=10)


def test_count_tokens_monotonic() -> None:
    a = count_tokens("alpha beta")
    b = count_tokens("alpha beta gamma delta epsilon")
    assert a < b
