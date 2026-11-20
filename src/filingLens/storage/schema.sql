CREATE TABLE IF NOT EXISTS schema_info(version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS company(ticker TEXT PRIMARY KEY, isin TEXT, name TEXT, sector TEXT, aliases TEXT);
CREATE TABLE IF NOT EXISTS document(
  doc_id TEXT PRIMARY KEY, ticker TEXT, isin TEXT, doc_type TEXT, fy TEXT, scope TEXT,
  source_url TEXT, local_path TEXT, original_name TEXT, parser TEXT, parser_ver TEXT,
  n_pages INTEGER, parse_quality DOUBLE PRECISION, meta_json TEXT, ingested_at TEXT);
CREATE INDEX IF NOT EXISTS ix_document_ticker ON document(ticker, doc_type);
CREATE TABLE IF NOT EXISTS xbrl_fact_raw(
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, doc_id TEXT, tag TEXT, ns TEXT, value_text TEXT,
  value_num DOUBLE PRECISION, unit TEXT, decimals TEXT, ctx_id TEXT, period_start TEXT, period_end TEXT,
  instant TEXT, has_dim INTEGER);
CREATE INDEX IF NOT EXISTS ix_raw_doc ON xbrl_fact_raw(doc_id, has_dim);
CREATE TABLE IF NOT EXISTS metric(metric_id TEXT PRIMARY KEY, description TEXT, kind TEXT, unit_kind TEXT, synonyms TEXT, tags TEXT);
CREATE TABLE IF NOT EXISTS fact(
  fact_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, isin TEXT, ticker TEXT, metric_id TEXT,
  period_type TEXT, fy TEXT, quarter TEXT, period_start TEXT, period_end TEXT, scope TEXT,
  value DOUBLE PRECISION, unit TEXT, orig_value DOUBLE PRECISION, orig_unit TEXT,
  xbrl_tag TEXT, doc_id TEXT, pdf_doc_id TEXT, page INTEGER, verified TEXT, qa TEXT, method TEXT,
  UNIQUE(ticker, metric_id, period_start, period_end, scope));
CREATE INDEX IF NOT EXISTS ix_fact_series ON fact(ticker, metric_id, scope, period_type);
CREATE TABLE IF NOT EXISTS page(doc_id TEXT, page_no INTEGER, text TEXT, scope_tag TEXT, section TEXT, is_scanned INTEGER,
  PRIMARY KEY(doc_id, page_no));
-- tsv: the keyword index (Postgres full-text search, English stemming), kept in sync by Postgres
CREATE TABLE IF NOT EXISTS chunk(
  chunk_id TEXT PRIMARY KEY, doc_id TEXT, ticker TEXT, fy TEXT, doc_type TEXT, scope TEXT, chunk_type TEXT, section TEXT,
  page_start INTEGER, page_end INTEGER, text TEXT, context_line TEXT, vec_idx INTEGER,
  tsv tsvector GENERATED ALWAYS AS
    (to_tsvector('english'::regconfig, coalesce(context_line, '') || ' ' || coalesce(text, ''))) STORED);
CREATE INDEX IF NOT EXISTS ix_chunk_tsv ON chunk USING GIN (tsv);
CREATE INDEX IF NOT EXISTS ix_chunk_doc ON chunk(doc_id);
CREATE TABLE IF NOT EXISTS validation(
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ticker TEXT, scope TEXT, period_end TEXT,
  check_name TEXT, status TEXT, detail TEXT);
-- chats: each user's conversations on the Ask page; a turn keeps the answer as shown (AnswerView JSON)
CREATE TABLE IF NOT EXISTS chat(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
  title_source TEXT NOT NULL DEFAULT 'question',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS ix_chat_user ON chat(user_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS chat_turn(
  chat_id TEXT NOT NULL REFERENCES chat(id) ON DELETE CASCADE, seq INTEGER NOT NULL,
  question TEXT NOT NULL, answer_json TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(chat_id, seq));
