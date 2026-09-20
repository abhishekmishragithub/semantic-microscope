"""Text -> sentences with exact char offsets into the source.

Paragraph-first: pysbd is run on each run of non-blank lines so a heading never merges with the
body below it, and an all-caps heading line is kept whole because pysbd splits "ARTICLE 6." off.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass

MIN_WORDS = 3
MAX_CHARS = 600  # pysbd gives up inside long curly-quoted passages; regex-split anything longer
_BLOCK = re.compile(r"(?:[^\n]*\S[^\n]*\n?)+")
_FALLBACK = re.compile(
    r"(?<!\bMr\.)(?<!\bMrs\.)(?<!\bMs\.)(?<!\bDr\.)(?<!\bSt\.)(?<!\bNo\.)"
    r"(?:(?<=[.!?])|(?<=[.!?][\"”’']))\s+(?=[\"“‘(]?[A-Z])"
)


@dataclass(frozen=True)
class Sentence:
    idx: int
    char_start: int
    char_end: int
    text: str

    @property
    def too_short(self) -> bool:
        return len(self.text.split()) < MIN_WORDS


def _segmenter():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        import pysbd
    return pysbd.Segmenter(language="en", clean=False, char_span=True)


def _is_heading(block: str) -> bool:
    return "\n" not in block.strip() and not any(c.islower() for c in block)


def _trim(text: str, start: int) -> tuple[int, int, str] | None:
    stripped = text.strip()
    if not stripped:
        return None
    lead = len(text) - len(text.lstrip())
    return start + lead, start + lead + len(stripped), stripped


def _resplit(pieces: list[tuple[int, str]]) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for offset, sent in pieces:
        if len(sent) <= MAX_CHARS:
            out.append((offset, sent))
            continue
        pos = 0
        for m in _FALLBACK.finditer(sent):
            out.append((offset + pos, sent[pos : m.start()]))
            pos = m.end()
        out.append((offset + pos, sent[pos:]))
    return out


def segment(text: str) -> list[Sentence]:
    seg = _segmenter()
    out: list[Sentence] = []
    for block in _BLOCK.finditer(text):
        raw = block.group(0)
        if _is_heading(raw):
            pieces = [(0, raw)]
        else:
            pieces = [(span.start, span.sent) for span in seg.segment(raw)]
        for offset, sent in _resplit(pieces):
            trimmed = _trim(sent, block.start() + offset)
            if trimmed is None:
                continue
            start, end, clean = trimmed
            assert text[start:end] == clean, f"offset drift at {start}"
            out.append(Sentence(idx=len(out), char_start=start, char_end=end, text=clean))
    return out
