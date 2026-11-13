# India Equity Research filingLens

Facts come from a verified database (XBRL), never from an LLM. See the V1 build plan.

## Status
- [x] Phase 1: `domain` (fiscal, units, models, registry.yaml)
- [x] Phase 2: `storage` (schema, db, FactRepo, ValidationRepo)
- [x] Phase 3: `ingest` (xbrl parse, canonical facts, validate, tag audit, pipeline, CLI)

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
