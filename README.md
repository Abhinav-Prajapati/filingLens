# India Equity Research filingLens

Facts come from a verified database (XBRL), never from an LLM. See the V1 build plan.

## Status
- [x] Phase 1: `domain` (fiscal, units, models, registry.yaml)
- [x] Phase 2: `storage` (schema, db, FactRepo, ValidationRepo)
- [x] Phase 3: `ingest` (xbrl parse, canonical facts, validate, tag audit, pipeline, CLI)
- [x] Phase 4: `ingest/pdf` + `ingest/calibrate` (pages, chunks, FTS, page citations)
- [x] Phase 5: `retrieval` (BGE-M3 embeddings in vectors.npy, FTS5 keyword, RRF hybrid search)

## Search
    uv sync --extra dev --extra embed          # torch + sentence-transformers (GPU if available)
    filinglens index                           # embed new chunks -> work/vectors.npy (+ .json)
    filinglens search "how are geopolitical risks managed?" --ticker TCS --section mdna -k 5
    pytest -m embed                            # recall@5 on tests/golden/recall.yaml (real model)

BGE-M3 runs in fp16 on a CUDA GPU (~1.5 GB VRAM incl. activations), fp32 on CPU otherwise.
Filters apply before ranking. Keyword queries drop stopwords (English + Hinglish) and RRF uses
k=10: recall@5 went from 7/11 (k=60, all words) to 9/11. Romanised Hinglish is the weak spot.

## Ingesting everything
    work/inbox/TCS/xbrl/*.xml          # result filings (NSE XBRL)
    work/inbox/TCS/annual_report/*.pdf # annual reports
    filinglens ingest work/inbox --db work/filingLens.db     # XBRL -> PDFs -> calibration
    filinglens inspect-pdf report.pdf [--page 91]            # no database: sections, chunks

PDFs are read with PyMuPDF. Two-page spreads are split into halves ("panels") so each
half gets its own section/scope from its running header. Tables are not extracted
(they are garbled on spreads); statement rows survive as text. Each FY/INSTANT fact is
matched to the page that prints it; an FY25 report also verifies FY24 facts (prior-year
column). TCS FY24: 14/14 facts verified, all on PDF page 91 (printed pp. 178-179).

## Parsing an XBRL filing
    filinglens inspect path/to/TCS_Annual_INDAS_....xml        # no database; prints facts, checks, audit
    filinglens inspect path/to/file.xml --json                 # same, machine-readable
    filinglens ingest-xbrl work/inbox/TCS/xbrl --db work/filingLens.db   # files or folders

In Python:

    from filingLens.domain.registry import load_registry
    from filingLens.ingest.xbrl import parse_xbrl_file, xbrl_meta, facts_from_raw, resolve_ticker
    reg = load_registry()
    parsed = parse_xbrl_file("TCS_Annual_INDAS_....xml")      # contexts, units, raw facts, warnings
    meta = xbrl_meta(parsed.facts)                              # scope, rounding, FY, symbol
    ticker = resolve_ticker(meta, "TCS_Annual_....xml", reg, parsed.entity_ids)
    filing = facts_from_raw(parsed.facts, meta, ticker, "doc-id", reg)   # canonical Fact list

**NSE quirk (the Phase 0 bug):** in NSE result files the `FourD` (year-to-date) context
carries the quarter's dates. The real period is in the `DateOfStartOfReportingPeriod`
fact reported in that context, so the parser overrides the period of every `Four*`
context and prints a warning. Without this, FY facts collapse into Q4 duplicates.

Money values in XBRL are absolute INR (`decimals="-7"`); canonical `value` = INR crore.

## Dev
    pip install -e ".[dev]"
    pytest
