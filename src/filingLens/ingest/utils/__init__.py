"""Generic helpers for ingestion, independent of XBRL semantics.

* ``files``   - read a document's bytes (unwrapping a ZIP), sha256 file identity
* ``xmltree`` - namespace-agnostic lxml helpers and a safe XML parser
* ``numeric`` - number parsing for plain XBRL text and inline XBRL elements
"""
