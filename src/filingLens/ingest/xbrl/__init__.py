"""XBRL ingestion: file -> raw facts -> canonical facts.

    parser.py  file -> ParsedFiling (contexts, units, raw facts)
    nse.py     NSE column conventions and the FourD period fix (Phase 0 bug)
    model.py   Context, ParsedFiling
    meta.py    XbrlMeta: scope, rounding, period, company
    facts.py   canonical facts per filing, merge across filings (restatements)

Import from here; the submodule layout is an implementation detail.
"""
from filingLens.ingest.errors import XbrlParseError
from filingLens.ingest.xbrl.facts import (
    BuildResult,
    FilingFacts,
    Restatement,
    build_facts,
    facts_from_raw,
)
from filingLens.ingest.xbrl.meta import XbrlMeta, is_result_filing, resolve_ticker, xbrl_meta
from filingLens.ingest.xbrl.model import Context, ParsedFiling
from filingLens.ingest.xbrl.parser import (
    PARSER_NAME,
    PARSER_VERSION,
    parse_xbrl_bytes,
    parse_xbrl_file,
)

__all__ = [
    "PARSER_NAME", "PARSER_VERSION", "BuildResult", "Context", "FilingFacts", "ParsedFiling",
    "Restatement", "XbrlMeta", "XbrlParseError", "build_facts", "facts_from_raw",
    "is_result_filing", "parse_xbrl_bytes", "parse_xbrl_file", "resolve_ticker", "xbrl_meta",
]
