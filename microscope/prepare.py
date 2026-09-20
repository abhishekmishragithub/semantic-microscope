"""URL, HTML or plain text -> clean .txt for `label`.

HTML: drop chrome (nav, header, footer, aside, script, style) and footnotes (elements whose
class/id says note/footnote/reference, and <sup> markers), turn block elements into paragraph
breaks, and join table cells on one line so numbered recitals read "(1) The protection ...".
Plain text: cut Project Gutenberg boilerplate, drop [Illustration] blocks, unwrap hard-wrapped
lines inside paragraphs, strip _underscore_ italics, normalise whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

import httpx

SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "template",
    "svg",
    "head",
    "form",
    "button",
}
BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "tr",
    "blockquote",
    "pre",
    "section",
    "article",
    "table",
    "ul",
    "ol",
    "dd",
    "dt",
    "hr",
    "figure",
    "figcaption",
}
CELL_TAGS = {"td", "th"}
NOTE_RE = re.compile(
    r"footnote|endnote|\bnote\b|oj-note|reference|references|citation|breadcrumb|cookie|menu|sidebar|toc\b|skip",
    re.IGNORECASE,
)
MARKER_RE = re.compile(r"^\[?[\divxlc]{1,4}\]?$|^[a-z]$", re.IGNORECASE)


@dataclass
class Report:
    source: str
    kind: str
    paragraphs: int = 0
    words: int = 0
    dropped_note_elements: int = 0
    dropped_sup_markers: int = 0
    dropped_chrome_elements: int = 0
    dropped_illustrations: int = 0
    gutenberg_trimmed: bool = False
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.kind}: {self.paragraphs} paragraphs, {self.words:,} words"]
        if self.kind == "html":
            parts.append(
                f"dropped {self.dropped_chrome_elements} chrome, {self.dropped_note_elements} note elements, {self.dropped_sup_markers} sup markers"
            )
        else:
            parts.append(
                f"gutenberg trimmed: {self.gutenberg_trimmed}, illustrations dropped: {self.dropped_illustrations}"
            )
        return "; ".join(parts + self.notes)


class _Extractor(HTMLParser):
    def __init__(self, report: Report) -> None:
        super().__init__(convert_charrefs=True)
        self.report = report
        self.out: list[str] = []
        self.skip_stack: list[str] = []  # tags whose subtree we are skipping
        self.cell_depth = 0
        self.row_depth = 0
        self.sup_buf: list[str] | None = None
        self.sup_has_anchor = False

    def _is_note(self, attrs: list[tuple[str, str | None]]) -> bool:
        blob = " ".join(v or "" for k, v in attrs if k in ("class", "id", "role", "epub:type"))
        return bool(NOTE_RE.search(blob))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip_stack:
            if tag not in ("br", "hr", "img", "input", "meta", "link", "col"):
                self.skip_stack.append(tag)
            return
        if tag in SKIP_TAGS:
            self.skip_stack.append(tag)
            self.report.dropped_chrome_elements += 1
            return
        if tag not in ("br", "hr", "img", "col", "meta", "link", "input") and self._is_note(attrs):
            self.skip_stack.append(tag)
            self.report.dropped_note_elements += 1
            return
        if tag == "sup":
            self.sup_buf, self.sup_has_anchor = [], False
            return
        if self.sup_buf is not None and tag == "a":
            self.sup_has_anchor = True
            return
        if tag == "tr":
            self.row_depth += 1
        if tag in CELL_TAGS:
            self.cell_depth += 1
            self.out.append(" ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in BLOCK_TAGS:
            self.out.append(" " if self.cell_depth and tag in ("p", "div") else "\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_stack:
            if self.skip_stack[-1] == tag:
                self.skip_stack.pop()
            elif tag in self.skip_stack:
                while self.skip_stack and self.skip_stack.pop() != tag:
                    pass
            return
        if tag == "sup" and self.sup_buf is not None:
            text = "".join(self.sup_buf).strip()
            if self.sup_has_anchor or MARKER_RE.match(text):
                self.report.dropped_sup_markers += 1
            else:
                self.out.append(text)
            self.sup_buf = None
            return
        if tag in CELL_TAGS:
            self.cell_depth = max(0, self.cell_depth - 1)
            self.out.append(" ")
        elif tag in BLOCK_TAGS:
            self.out.append(" " if self.cell_depth and tag in ("p", "div") else "\n\n")
        if tag == "tr":
            self.row_depth = max(0, self.row_depth - 1)

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        if self.sup_buf is not None:
            self.sup_buf.append(data)
            return
        if self.row_depth:
            data = data.replace("\n", " ")  # whitespace between cells must not split the row
        self.out.append(data)


def normalise(text: str) -> str:
    text = text.replace("﻿", "").replace("\xa0", " ").replace(" ", "\n")
    paras = []
    for block in re.split(r"\n\s*\n", text):
        p = re.sub(r"[ \t\r\f\v]+", " ", block.replace("\n", " ")).strip()
        if p:
            paras.append(p)
    return "\n\n".join(paras) + "\n"


def clean_html(html: str, report: Report) -> str:
    ex = _Extractor(report)
    ex.feed(html)
    ex.close()
    text = "".join(ex.out)
    text, n = re.subn(r"\s?\(\s*\)", "", text)  # parentheses emptied by a dropped footnote marker
    if n:
        report.notes.append(f"removed {n} empty () left by footnote markers")
    return normalise(text)


GUT_START = re.compile(
    r"^\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*\s*$", re.MULTILINE
)
GUT_END = re.compile(
    r"^\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*\s*$", re.MULTILINE
)
ILLUSTRATION = re.compile(
    r"\[Illustration:?(?:[^\[\]]|\[[^\[\]]*\])*\]", re.DOTALL
)  # one nesting level
# Gutenberg layout markers such as "/* NIND “My dear friend, */" or a bare "/*" line: keep the text, drop the marker.
TRANSCRIBER = re.compile(r"/\*\s*(?:NIND|RIGHT|LEFT|CENTER|CENTRE)?\s*|\s*\*/")


TABLE_BORDER = re.compile(r"^\s*\+[-=+]+\+\s*$", re.MULTILINE)
TABLE_ROW = re.compile(r"^\s*\|(.*)\|\s*$", re.MULTILINE)


def _table_row(m: re.Match[str]) -> str:
    cells = [c.strip() for c in m.group(1).split("|")]
    return "\n" + ", ".join(c for c in cells if c) + "\n"


def clean_text(text: str, report: Report) -> str:
    m1, m2 = GUT_START.search(text), GUT_END.search(text)
    if m1 and m2 and m2.start() > m1.end():
        # Gutenberg-only cleanups. Elsewhere "*/" is a media range and "_" is an identifier.
        text = text[m1.end() : m2.start()]
        report.gutenberg_trimmed = True
        text, n = ILLUSTRATION.subn("", text)
        report.dropped_illustrations = n
        text, n = TRANSCRIBER.subn("", text)
        if n:
            report.notes.append(f"removed {n} /* */ transcriber markers")
        n = text.count("_")
        if n:
            text = text.replace("_", "")  # italics markers, sometimes spanning lines
            report.notes.append(f"stripped {n} italic underscores")
    text, n_border = TABLE_BORDER.subn("\n", text)
    text, n_rows = TABLE_ROW.subn(_table_row, text)
    if n_border or n_rows:
        report.notes.append(
            f"flattened ASCII tables: {n_border} border lines, {n_rows} rows -> one paragraph per row"
        )
    return normalise(text)


def slug(source: str) -> str:
    base = source.rsplit("/", 1)[-1] if "://" in source else Path(source).stem
    base = re.sub(r"\.(html?|txt|xml)$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"[^A-Za-z0-9]+", "-", base).strip("-").lower()
    return base or "document"


def cut(text: str, start_at: str | None, end_at: str | None, report: Report) -> str:
    """Keep from the first paragraph starting with start_at up to (not including) the first
    paragraph starting with end_at after it. For trimming prefaces, contents lists, imprints."""
    paras = text.split("\n\n")
    lo, hi = 0, len(paras)
    if start_at:
        # An exact paragraph match wins over a prefix match, so a heading beats a table of
        # contents paragraph that happens to begin with the same words.
        hits = [i for i, p in enumerate(paras) if p == start_at] or [
            i for i, p in enumerate(paras) if p.startswith(start_at)
        ]
        if hits:
            lo = hits[0]
            report.notes.append(f"cut {lo} paragraphs before {start_at!r}")
        else:
            report.notes.append(f"start-at {start_at!r} not found; nothing cut at the front")
    if end_at:
        # Last occurrence after the start, so a printer's imprint that appears both at the front
        # and the back of a book cuts at the back.
        hits = [i for i, p in enumerate(paras) if i > lo and p.startswith(end_at)]
        if hits:
            hi = hits[-1]
            report.notes.append(f"cut {len(paras) - hi} paragraphs from {end_at!r} on")
        else:
            report.notes.append(f"end-at {end_at!r} not found; nothing cut at the back")
    return "\n\n".join(paras[lo:hi]).strip() + "\n"


def prepare(
    source: str, start_at: str | None = None, end_at: str | None = None
) -> tuple[str, Report]:
    if source.startswith(("http://", "https://")):
        r = httpx.get(
            source,
            follow_redirects=True,
            timeout=30.0,
            headers={"User-Agent": "Mozilla/5.0 semantic-microscope"},
        )
        r.raise_for_status()
        raw = r.text
        is_html = "html" in r.headers.get("content-type", "") or raw.lstrip()[
            :200
        ].lower().startswith(("<!doctype", "<html"))
    else:
        raw = Path(source).read_text(encoding="utf-8", errors="replace")
        is_html = Path(source).suffix.lower() in (".html", ".htm", ".xhtml") or raw.lstrip()[
            :200
        ].lower().startswith(("<!doctype", "<html"))
    report = Report(source=source, kind="html" if is_html else "text")
    text = clean_html(raw, report) if is_html else clean_text(raw, report)
    if start_at or end_at:
        text = cut(text, start_at, end_at, report)
    report.paragraphs = text.count("\n\n") + 1
    report.words = len(text.split())
    return text, report
