"""Annual-report PDF -> pages and chunks, by rules (no LLM).

Indian annual reports are often published as two-page spreads: one PDF page holds
two printed pages, each with its own running header ("Consolidated Financial
Statements 2024-25", printed page number). Each half is read as a separate *panel*,
so section and scope come from that half's own header. A page that is not a spread
is one panel.

Table extraction is deliberately not used: on spreads it merges cells across
columns and costs ~2 s per page, while the plain text already keeps each row as
"label / note / this year / last year", which is what search and verification need.

Text cleanup (fonts, not content, cause these):
* ligature glyphs (ﬁ, ﬂ) are expanded by PyMuPDF; Unicode NFKC is NOT used because it
  would turn superscript footnote markers into digits ("1,234²" -> "12342");
* words split beside a ligature ("Secti on", "fi nancial") are rejoined when the joined
  spelling is at least as common in the same document as the split one ("cash flow" stays);
* Indian reports draw ₹ with a "Rupee" font at the backtick code point: "`" -> "₹".
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from filingLens.domain.models import Chunk, Page

PARSER_NAME = "pymupdf"
PARSER_VERSION = "3"   # bump to re-parse stored PDFs
DOC_LABEL = {"annual_report": "Annual Report", "brsr": "BRSR", "transcript": "Earnings call",
             "presentation": "Investor presentation"}

# heading patterns, searched in the panel's heading lines (see heading_text); the LAST match
# wins, because the running header comes first and the panel's own title after it
_SECTIONS = [
    ("notice", r"\bnotice\b"),
    ("directors_report", r"board.?s report|directors.? report"),
    ("mdna", r"management.?s? discussion"),           # "Management’s discussion" (Infosys)
    ("corporate_governance", r"corporate governance"),
    ("brsr", r"business responsibility"),
    ("auditor_report", r"independent auditor"),
    ("financial_statements", r"financial statements|balance sheet|statement of profit and loss"),
    ("sustainability", r"sustainability disclosures"),
]
_HEAD_LINES = 6
_CHARS = str.maketrans({"`": "₹", "\xa0": " ", "\u2002": " ", "\u2003": " ", "\u2009": " ",
                        "\u202f": " "})
_LIG = r"(?:ffi|ffl|ff|fi|fl|ft|tt|ti)"
_SPLITS = (re.compile(rf"\b([A-Za-z]*{_LIG}) ([a-z]+)\b"),   # "Secti on", "fi nancial"
           re.compile(rf"\b([A-Za-z]+) ({_LIG}[a-z]*)\b"))   # "Arti ficial"
_SCOPE_IN_HEAD = re.compile(r"(consolidated|standalone) financial statements", re.IGNORECASE)


@dataclass
class Panel:
    page_no: int
    side: str                     # "L", "R", or "" for a single page
    blocks: list[str]
    printed: str | None = None    # printed page number from the header
    section: str = "front_matter"
    scope: str = "none"
    scanned: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.blocks)


@dataclass
class ParsedPdf:
    fy: str | None
    pages: list[Page]
    chunks: list[Chunk]
    panels: list[Panel] = field(default_factory=list)

    @property
    def parse_quality(self) -> float:
        """Share of pages with real text (>= 200 chars)."""
        return round(sum(len(p.text) >= 200 for p in self.pages) / max(len(self.pages), 1), 3)


# ---------------------------------------------------------------------------------------
# reading (the only PyMuPDF code)
# ---------------------------------------------------------------------------------------
def read_panels(path: str | Path, max_pages: int | None = None) -> list[Panel]:
    import pymupdf

    flags = pymupdf.TEXTFLAGS_BLOCKS & ~pymupdf.TEXT_PRESERVE_LIGATURES   # ﬁ -> fi
    panels: list[Panel] = []
    with pymupdf.open(str(path)) as doc:
        for i, pg in enumerate(doc):
            if max_pages and i >= max_pages:
                break
            r = pg.rect
            halves = ([("L", pymupdf.Rect(0, 0, r.width / 2, r.height)),
                       ("R", pymupdf.Rect(r.width / 2, 0, r.width, r.height))]
                      if r.width > r.height else [("", r)])
            for side, clip in halves:
                blocks = [re.sub(r"[ \t]+", " ", b[4].translate(_CHARS)).strip()
                          for b in pg.get_text("blocks", clip=clip, sort=True, flags=flags)
                          if b[6] == 0]
                blocks = [b for b in blocks if b]
                scanned = sum(map(len, blocks)) < 50 and bool(pg.get_images())
                panels.append(Panel(i + 1, side, blocks, scanned=scanned))
    return panels


def rejoin_split_words(panels: list[Panel]) -> list[Panel]:
    """Join "Secti on" -> "Section" when the joined word appears in the same document at
    least as often as the split pair. Two passes: the space after a ligature, then before."""
    text = "\n".join(p.text for p in panels)
    words = Counter(w.lower() for w in re.findall(r"[A-Za-z]+", text))
    for pat in _SPLITS:
        pairs = Counter(f"{a} {b}".lower() for a, b in pat.findall(text))

        def join(m: re.Match[str], pairs: Counter[str] = pairs) -> str:
            a, b = m.group(1), m.group(2)
            n = words[(a + b).lower()]
            return a + b if n and n >= pairs[f"{a} {b}".lower()] else m.group(0)

        for p in panels:
            p.blocks = [pat.sub(join, b) for b in p.blocks]
        text = "\n".join(p.text for p in panels)
    return panels


# ---------------------------------------------------------------------------------------
# classification
# ---------------------------------------------------------------------------------------
def scope_from_counts(text: str) -> str:
    t = text.lower()
    c, s = t.count("consolidated"), t.count("standalone")
    if c == 0 and s == 0:
        return "none"
    if c > s * 1.5:
        return "consolidated"
    if s > c * 1.5:
        return "standalone"
    return "mixed"


def printed_page(text: str) -> str | None:
    """First line of the panel's header that is just a number (e.g. '179')."""
    for line in text.splitlines()[:6]:
        if re.fullmatch(r"\d{1,3}", line.strip()):
            return line.strip()
    return None


def heading_text(text: str) -> str:
    """The panel's heading lines: among the first few lines, those that are short and start
    with a capital letter. Wrapped body text is long or starts lowercase and is skipped."""
    lines = [ln.strip() for ln in text.splitlines()[:_HEAD_LINES]]
    return " ".join(ln for ln in lines if len(ln) <= 90 and ln[:1].isupper()).lower()


def classify(panels: list[Panel]) -> list[Panel]:
    """Set section (carried forward when a panel has no header), scope and printed page."""
    section = "front_matter"
    for p in panels:
        text = p.text
        head = heading_text(text)
        hits = [(m.start(), name) for name, pat in _SECTIONS for m in re.finditer(pat, head)]
        if hits:
            section = max(hits)[1]
        p.section = section
        scopes = _SCOPE_IN_HEAD.findall(head)
        p.scope = scopes[-1].lower() if scopes else scope_from_counts(text)
        p.printed = printed_page(text)
    return panels


def infer_fy_pdf(texts: list[str]) -> str | None:
    """Most common FY mentioned in the first pages: '2024-25' or 'March 31, 2025' -> FY25."""
    blob = "\n".join(texts)
    c: Counter[str] = Counter()
    for _a, b in re.findall(r"\b(20\d{2})\s*[-–—/]\s*(\d{2}|20\d{2})\b", blob):
        c[f"FY{int(b[-2:]):02d}"] += 1
    for y in re.findall(r"(?:31(?:st)?\s+March,?|March\s+31(?:st)?,?)\s+(20\d{2})", blob, re.IGNORECASE):
        c[f"FY{int(y) % 100:02d}"] += 1
    return c.most_common(1)[0][0] if c else None


# ---------------------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------------------
def pack_blocks(blocks: list[str], max_chars: int = 1100, min_chars: int = 80) -> list[str]:
    """Greedy-pack text blocks into chunks of at most max_chars; long blocks split at
    sentence ends. Chunks shorter than min_chars (page furniture) are dropped."""
    pieces: list[str] = []
    for b in blocks:
        if len(b) <= max_chars:
            pieces.append(b)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?])\s+", b):
            while len(sent) > max_chars:
                pieces.append(sent[:max_chars])
                sent = sent[max_chars:]
            if cur and len(cur) + 1 + len(sent) > max_chars:
                pieces.append(cur)
                cur = sent
            else:
                cur = f"{cur} {sent}" if cur else sent
        if cur:
            pieces.append(cur)
    out: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) > max_chars:
            out.append(cur)
            cur = piece
        else:
            cur = f"{cur}\n{piece}" if cur else piece
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= min_chars]


def _combine_scope(scopes: set[str]) -> str:
    real = scopes - {"none"}
    return real.pop() if len(real) == 1 else ("mixed" if real else "none")


def build_document(panels: list[Panel], doc_id: str, ticker: str, company: str,
                   doc_type: str, fy: str | None) -> ParsedPdf:
    """Panels -> page rows (one per PDF page) and chunks (per panel, never across pages)."""
    label = f"{DOC_LABEL.get(doc_type, doc_type)} {fy or ''}".strip()
    by_page: dict[int, list[Panel]] = {}
    for p in panels:
        by_page.setdefault(p.page_no, []).append(p)
    pages, chunks = [], []
    for pno, ps in by_page.items():
        pages.append(Page(doc_id, pno, "\n".join(p.text for p in ps),
                          _combine_scope({p.scope for p in ps}), ps[0].section,
                          int(all(p.scanned for p in ps))))
        for p in ps:
            ctx = (f"{company} | {label} | {p.section} | scope: {p.scope} | page {pno}"
                   + (f" (p. {p.printed})" if p.printed else ""))
            for idx, text in enumerate(pack_blocks(p.blocks)):
                cid = hashlib.sha1(f"{doc_id}|{pno}|{p.side}|{idx}|{text}".encode()).hexdigest()
                chunks.append(Chunk(cid[:16], doc_id, ticker, fy, doc_type, p.scope, "text",
                                    p.section, pno, pno, text, ctx))
    return ParsedPdf(fy, pages, chunks, panels)


def parse_pdf(path: str | Path, doc_id: str, ticker: str, company: str,
              doc_type: str = "annual_report", fy: str | None = None,
              max_pages: int | None = None) -> ParsedPdf:
    panels = classify(rejoin_split_words(read_panels(path, max_pages)))
    fy = fy or infer_fy_pdf([p.text for p in panels[:8]])
    return build_document(panels, doc_id, ticker, company, doc_type, fy)
