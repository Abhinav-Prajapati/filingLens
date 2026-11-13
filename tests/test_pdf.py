"""PDF reading, classification and chunking on a generated report (see conftest.report_pdf)."""
from filingLens.ingest.pdf import (
    Panel,
    classify,
    heading_text,
    infer_fy_pdf,
    pack_blocks,
    parse_pdf,
    rejoin_split_words,
)


def test_spread_split_into_panels_with_own_headers(report_pdf):
    r = parse_pdf(report_pdf, "d", "TCS", "Tata Consultancy Services")
    got = [(p.page_no, p.side, p.printed, p.section, p.scope) for p in r.panels]
    assert got == [(1, "L", "178", "financial_statements", "consolidated"),
                   (1, "R", "179", "financial_statements", "consolidated"),
                   (2, "", "86", "mdna", "none"),
                   (3, "", "240", "financial_statements", "standalone")]
    assert r.fy == "FY24" and len(r.pages) == 3
    assert r.pages[0].scope_tag == "consolidated" and "Total assets" in r.pages[0].text


def test_chunks_stay_on_their_page_and_panel(report_pdf):
    r = parse_pdf(report_pdf, "d", "TCS", "Tata Consultancy Services")
    assert all(c.page_start == c.page_end for c in r.chunks)
    pl = next(c for c in r.chunks if "Statement of Profit and Loss" in c.text)
    assert "Total assets" not in pl.text                      # left half is a separate chunk
    assert pl.context_line == ("Tata Consultancy Services | Annual Report FY24 | "
                               "financial_statements | scope: consolidated | page 1 (p. 179)")
    assert len({c.chunk_id for c in r.chunks}) == len(r.chunks)
    again = parse_pdf(report_pdf, "d", "TCS", "Tata Consultancy Services")
    assert [c.chunk_id for c in again.chunks] == [c.chunk_id for c in r.chunks]   # stable ids


def test_rupee_font_backtick_becomes_rupee_sign(report_pdf):
    text = "\n".join(c.text for c in parse_pdf(report_pdf, "d", "TCS", "TCS").chunks)
    assert "(₹ crore)" in text and "`" not in text


def test_rejoin_split_words_uses_document_vocabulary():
    p = Panel(1, "", ["Secti on 135 and fi nancial statements. Arti ficial intelligence.",
                      "Section financial artificial. cash flow, cash flow, cashflow, ti me, time"])
    out = rejoin_split_words([p])[0].text
    assert "Section 135 and financial statements. Artificial intelligence." in out
    assert "cash flow, cash flow, cashflow" in out      # split pair more common: kept apart
    assert "time, time" in out
    q = rejoin_split_words([Panel(1, "", ["Secti on only appears split"])])[0].text
    assert q == "Secti on only appears split"            # never seen whole: left alone


def test_headings_ignore_body_text_and_carry_forward():
    body = ("The discussions in this section relate to the consolidated financial statements "
            "of the Company for the year")
    ps = classify([Panel(1, "", ["Management Discussion and Analysis", "86", body]),
                   Panel(2, "", ["financial statements included in this report are audited",
                                 "and more text"])])
    assert [p.section for p in ps] == ["mdna", "mdna"]
    assert heading_text("Notice\n67\nBoard’s Report\nTo the Members") == (
        "notice board’s report to the members")


def test_infer_fy_and_pack_blocks():
    assert infer_fy_pdf(["2024-25 Integrated Annual Report", "as at March 31, 2025"]) == "FY25"
    assert infer_fy_pdf(["no years here"]) is None
    long = "A sentence that is fairly long. " * 80
    chunks = pack_blocks([long, "x" * 10])
    assert all(len(c) <= 1100 for c in chunks) and len(chunks) == 3
    assert pack_blocks(["tiny"]) == []                          # page furniture dropped
