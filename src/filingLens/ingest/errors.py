"""Errors raised while reading source documents. Phase 4 adds a PDF error here."""
from __future__ import annotations


class IngestError(ValueError):
    """A source file cannot be used (empty, wrong format, broken archive)."""


class XbrlParseError(IngestError):
    """The file is not a usable XBRL / iXBRL document."""
