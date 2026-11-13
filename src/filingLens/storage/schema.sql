CREATE TABLE IF NOT EXISTS company(ticker TEXT PRIMARY KEY, isin TEXT, name TEXT, sector TEXT, aliases TEXT);
CREATE TABLE IF NOT EXISTS document(
  doc_id TEXT PRIMARY KEY, ticker TEXT, isin TEXT, doc_type TEXT, fy TEXT, scope TEXT,
  source_url TEXT, local_path TEXT, original_name TEXT, parser TEXT, parser_ver TEXT,
  n_pages INTEGER, parse_quality REAL, meta_json TEXT, ingested_at TEXT);
CREATE TABLE IF NOT EXISTS xbrl_fact_raw(
  id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, tag TEXT, ns TEXT, value_text TEXT, value_num REAL,
  unit TEXT, decimals TEXT, ctx_id TEXT, period_start TEXT, period_end TEXT, instant TEXT, has_dim INTEGER);
CREATE INDEX IF NOT EXISTS ix_raw_doc ON xbrl_fact_raw(doc_id, has_dim);
CREATE TABLE IF NOT EXISTS metric(metric_id TEXT PRIMARY KEY, description TEXT, kind TEXT, unit_kind TEXT, synonyms TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS fact(
  fact_id INTEGER PRIMARY KEY AUTOINCREMENT, isin TEXT, ticker TEXT, metric_id TEXT, period_type TEXT, fy TEXT, quarter TEXT,
  period_start TEXT, period_end TEXT, scope TEXT, value REAL, unit TEXT, orig_value REAL, orig_unit TEXT,
  xbrl_tag TEXT, doc_id TEXT, pdf_doc_id TEXT, page INTEGER, verified TEXT, qa TEXT, method TEXT,
  UNIQUE(ticker, metric_id, period_start, period_end, scope));
CREATE TABLE IF NOT EXISTS page(doc_id TEXT, page_no INTEGER, text TEXT, scope_tag TEXT, section TEXT, is_scanned INTEGER,
  PRIMARY KEY(doc_id, page_no));
CREATE TABLE IF NOT EXISTS chunk(
  chunk_id TEXT PRIMARY KEY, doc_id TEXT, ticker TEXT, fy TEXT, doc_type TEXT, scope TEXT, chunk_type TEXT, section TEXT,
  page_start INTEGER, page_end INTEGER, text TEXT, context_line TEXT, vec_idx INTEGER);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED, body, tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS validation(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, scope TEXT, period_end TEXT, check_name TEXT, status TEXT, detail TEXT);
