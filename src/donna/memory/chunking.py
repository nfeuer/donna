"""Markdown-aware chunker for the memory layer.

Token counting is routed through :func:`count_tokens`. We try
``tiktoken cl100k_base`` first (the neutral codebase-wide counter
that approximates MiniLM's WordPiece tokenizer within ~10% on English
prose). When the tiktoken encoding can't be loaded — offline dev
machines, CI without egress — we fall back to a deterministic
word+punct heuristic scaled by 1.3 to match BERT's average
WordPiece-per-word ratio. Both are ~10% off the real MiniLM
tokenizer, so the 256-token cap is a soft target; chunks may come in
slightly under the real model window and we accept that rather than
risk silent truncation inside the encoder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
_CODE_FENCE_RE = re.compile(r"^```")


def _trim(path: list[str]) -> list[str]:
    """Drop trailing empty levels so paths compare meaningfully."""
    out = list(path)
    while out and not out[-1]:
        out.pop()
    return out


def _path_related(a: list[str], b: list[str]) -> bool:
    """True when one trimmed heading path is a prefix of the other."""
    ta = _trim(a)
    tb = _trim(b)
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return longer[: len(shorter)] == shorter


def _longer_path(a: list[str], b: list[str]) -> list[str]:
    return a if len(_trim(a)) >= len(_trim(b)) else b
# Word-or-single-punct atoms, matches BERT WordPiece granularity well
# enough for a token-count proxy.
_FALLBACK_TOKEN_RE = re.compile(r"\w+|[^\w\s]")


def _try_load_tiktoken_encoder() -> Any | None:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return None


_ENCODING: Any | None = _try_load_tiktoken_encoder()


def count_tokens(text: str) -> int:
    """Approximate the MiniLM WordPiece token count for ``text``.

    Uses tiktoken when available, otherwise a deterministic
    word+punct heuristic scaled by 1.3.
    """
    if _ENCODING is not None:
        return len(_ENCODING.encode(text, disallowed_special=()))
    # Fallback: count word chunks + punctuation, scale up to match
    # BERT's ~1.3 WordPieces-per-word on English. Off by ~10% but
    # deterministic and offline-safe.
    raw = len(_FALLBACK_TOKEN_RE.findall(text))
    return max(1, int(raw * 1.3)) if raw else 0


def _encode(text: str) -> list[int]:
    if _ENCODING is not None:
        return _ENCODING.encode(text, disallowed_special=())
    return list(range(count_tokens(text)))


def _decode_tail(text: str, n_tokens: int) -> str:
    """Return the tail of ``text`` containing roughly ``n_tokens``.

    With tiktoken we slice + decode precisely. With the fallback we
    approximate by character count (BERT tokens average ~4 chars).
    """
    if _ENCODING is not None:
        tokens = _ENCODING.encode(text, disallowed_special=())
        if len(tokens) <= n_tokens:
            return text
        return _ENCODING.decode(tokens[-n_tokens:])
    approx_chars = n_tokens * 4
    return text[-approx_chars:] if len(text) > approx_chars else text


def _token_window_split(text: str, max_tokens: int, overlap: int) -> list[str]:
    step = max_tokens - overlap
    out: list[str] = []
    if _ENCODING is not None:
        tokens = _ENCODING.encode(text, disallowed_special=())
        for start in range(0, len(tokens), step):
            window = tokens[start : start + max_tokens]
            if not window:
                break
            out.append(_ENCODING.decode(window))
            if start + max_tokens >= len(tokens):
                break
        return out
    # Fallback: proportional character window (approximate).
    approx_step = max(step * 4, 1)
    approx_max = max(max_tokens * 4, 1)
    for start in range(0, len(text), approx_step):
        window = text[start : start + approx_max]
        if not window:
            break
        out.append(window)
        if start + approx_max >= len(text):
            break
    return out


@dataclass(frozen=True)
class Chunk:
    """One chunk emitted by a :class:`Chunker`."""

    index: int
    content: str
    heading_path: list[str]
    token_count: int


class Chunker(Protocol):
    """Protocol every chunker implements."""

    def chunk(self, body: str) -> list[Chunk]: ...


class MarkdownHeadingChunker:
    """Split on H1/H2/H3, preserve heading context, keep code fences intact.

    Rules:

    1. Walk lines, maintaining a 3-deep heading stack
       ``[h1, h2, h3]`` (empty string where a level is absent).
    2. Fenced code blocks (``\\`\\`\\``` to ``\\`\\`\\```) are treated as
       one atom and never split across chunks — unless the fence
       itself is larger than ``max_tokens``, in which case a token-
       window split is used as a last resort.
    3. Bodies between headings are split into paragraph atoms
       (blank-line separated) and greedily packed into chunks up to
       ``max_tokens``. On overflow the chunk is emitted and the next
       chunk is seeded with the last ``overlap_tokens`` tokens of the
       previous content (so nearest-neighbor queries that straddle a
       boundary still pull back at least one of the pair).
    4. Sections shorter than ``min_tokens`` merge forward into the
       previous chunk (same ``heading_path``) — short orphan sections
       add noise to the index.
    """

    def __init__(
        self,
        max_tokens: int = 256,
        overlap_tokens: int = 32,
        min_tokens: int = 32,
    ) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if overlap_tokens < 0 or overlap_tokens >= max_tokens:
            raise ValueError("overlap_tokens must be in [0, max_tokens)")
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens
        self.min_tokens = min_tokens

    def chunk(self, body: str) -> list[Chunk]:
        sections = self._split_sections(body)
        chunks: list[Chunk] = []
        for heading_path, section_text in sections:
            section_text = section_text.strip("\n")
            if not section_text:
                continue
            section_tokens = count_tokens(section_text)
            # Merge-forward: short orphan sections fold into the prior
            # chunk only when one heading path is a prefix of the
            # other (i.e. they share a common ancestor section). The
            # merged chunk keeps the more-specific path so we don't
            # lose provenance. Sections with independent paths stay
            # separate even if short, because their heading is the
            # only handle a reader has on them.
            if (
                chunks
                and section_tokens < self.min_tokens
                and chunks[-1].token_count + section_tokens <= self.max_tokens
                and _path_related(chunks[-1].heading_path, heading_path)
            ):
                prev = chunks[-1]
                merged_content = prev.content + "\n\n" + section_text
                merged_path = _longer_path(prev.heading_path, heading_path)
                chunks[-1] = Chunk(
                    index=prev.index,
                    content=merged_content,
                    heading_path=merged_path,
                    token_count=count_tokens(merged_content),
                )
                continue
            for packed in self._pack_section(section_text):
                chunks.append(
                    Chunk(
                        index=len(chunks),
                        content=packed,
                        heading_path=heading_path,
                        token_count=count_tokens(packed),
                    )
                )
        return chunks

    # -- internals ----------------------------------------------------

    def _split_sections(self, body: str) -> list[tuple[list[str], str]]:
        """Walk lines and group by heading path.

        A fenced code block is stitched into whichever section it
        belongs to. Headings encountered inside a code fence are
        ignored.
        """
        stack: list[str] = ["", "", ""]
        current: list[str] = []
        sections: list[tuple[list[str], str]] = [(list(stack), "")]
        in_fence = False
        for line in body.splitlines():
            if _CODE_FENCE_RE.match(line):
                in_fence = not in_fence
                current.append(line)
                continue
            if not in_fence:
                m = _HEADING_RE.match(line)
                if m:
                    # Flush the current section before switching.
                    if current:
                        sections[-1] = (sections[-1][0], "\n".join(current))
                        current = []
                    level = len(m.group(1))
                    title = m.group(2).strip()
                    # Update the stack; clear deeper levels.
                    stack = list(stack)
                    stack[level - 1] = title
                    for i in range(level, 3):
                        stack[i] = ""
                    sections.append((list(stack), ""))
                    continue
            current.append(line)
        if current:
            sections[-1] = (sections[-1][0], "\n".join(current))
        # Drop empty leading section when the doc opens with a heading.
        return [(h, t) for (h, t) in sections if t.strip()]

    def _pack_section(self, text: str) -> list[str]:
        """Greedy-pack atoms (paragraphs / fences) into max_tokens chunks."""
        atoms = self._atoms(text)
        packed: list[str] = []
        buf: list[str] = []
        buf_tokens = 0
        for atom in atoms:
            atom_tokens = count_tokens(atom)
            if atom_tokens > self.max_tokens:
                # Atom itself is too big — flush and split it.
                if buf:
                    packed.append("\n\n".join(buf))
                    buf, buf_tokens = [], 0
                packed.extend(self._split_oversized(atom))
                continue
            if buf_tokens + atom_tokens > self.max_tokens and buf:
                packed.append("\n\n".join(buf))
                # Seed next chunk with the overlap tail of the prior.
                overlap = self._overlap_tail(packed[-1])
                buf = [overlap] if overlap else []
                buf_tokens = count_tokens(overlap) if overlap else 0
            buf.append(atom)
            buf_tokens += atom_tokens
        if buf:
            packed.append("\n\n".join(buf))
        return packed

    def _atoms(self, text: str) -> list[str]:
        """Paragraph-granularity split that never cuts a code fence."""
        atoms: list[str] = []
        buf: list[str] = []
        in_fence = False
        for line in text.splitlines():
            if _CODE_FENCE_RE.match(line):
                buf.append(line)
                in_fence = not in_fence
                continue
            if not in_fence and not line.strip() and buf:
                atoms.append("\n".join(buf).strip("\n"))
                buf = []
                continue
            buf.append(line)
        if buf:
            atoms.append("\n".join(buf).strip("\n"))
        return [a for a in atoms if a.strip()]

    def _overlap_tail(self, chunk_text: str) -> str:
        if self.overlap_tokens <= 0:
            return ""
        return _decode_tail(chunk_text, self.overlap_tokens)

    def _split_oversized(self, atom: str) -> list[str]:
        """Last-resort token-window split for atoms larger than max_tokens."""
        return _token_window_split(atom, self.max_tokens, self.overlap_tokens)


__all__ = [
    "Chunk",
    "Chunker",
    "MarkdownHeadingChunker",
    "count_tokens",
]
