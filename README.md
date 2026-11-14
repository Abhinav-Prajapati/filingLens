# India Equity Research filingLens

Facts come from a verified database (XBRL), never from an LLM. See the V1 build plan.

## Status
- [x] Phase 1: `domain` (fiscal, units, models, registry.yaml)
- [x] Phase 2: `storage` (Postgres schema, db, FactRepo, ValidationRepo, S3 object store)
- [x] Phase 3: `ingest` (xbrl parse, canonical facts, validate, tag audit, pipeline, CLI)
- [x] Phase 4: `ingest/pdf` + `ingest/calibrate` (pages, chunks, FTS, page citations)
- [x] Phase 5: `retrieval` (BGE-M3 embeddings in vectors.npy, Postgres full-text keyword, RRF hybrid search)
- [x] Phase 6: `analytics` (YoY, CAGR, margins, trends, unit-safe comparisons)
- [x] LLM planner (`llm/`): question -> one validated tool call (OpenAI-compatible API)
- [x] Rule planner + answer service (`nlu/`, `service.py`): works without an LLM
- [x] Eval set + harness (`evals/`, `evaluation.py`)
- [x] HTTP API with typed, chart-ready views (`api/`, `views.py`)

## Running with Docker

Four services (`docker-compose.yml`), every port on 127.0.0.1:

| service | what | port |
|---|---|---|
| `postgres` | all data: figures, report text and its keyword index, accounts | 5442 |
| `seaweedfs` | S3-compatible store for downloaded filings (bucket `filinglens`) | 8333 (S3 API) |
| `s3manager` | small web UI to browse and download the bucket's files | http://127.0.0.1:8090 |
| `app` | the API and web UI (GPU for BGE-M3; host network, so a local llama-server works as configured) | http://127.0.0.1:8765 |

```bash
cp .env.example .env              # set the passwords / keys (and the LLM, optional)
docker compose up -d --build      # first build downloads CUDA torch: several GB
docker compose exec app filinglens companies --refresh       # NSE's list of listed companies
docker compose exec -it app filinglens users create-admin you@example.com
```

Open http://127.0.0.1:8765, sign in, and add companies from **Admin → Data** (Download).

Infrastructure only, with the app on the host: `docker compose up -d postgres seaweedfs s3manager`,
then `filinglens serve` (it reads `FILINGLENS_DATABASE_URL` and `FILINGLENS_S3_*` from `.env`).
Stop a host `filinglens serve` before starting the `app` service: both use port 8765.

## Quickstart (host)
    docker compose up -d postgres seaweedfs s3manager           # Postgres + S3 (see above)
    uv sync --extra dev --extra llm --extra api --extra embed   # embed: BGE-M3 (~2 GB, GPU optional)
    source .venv/bin/activate
    filinglens companies                    # download NSE's list of listed companies (once)
    filinglens download TCS INFY HINDUNILVR # results XBRL + annual reports -> work/inbox
    filinglens ingest work/inbox            # facts, checks, report pages, PDF verification
    filinglens index                        # embeddings for report search
    filinglens status                       # what you have, per company
    filinglens ask "TCS vs Infosys net margin" --no-llm
    cp .env.example .env                    # optional: an LLM for harder questions
    filinglens serve                        # HTTP API: http://127.0.0.1:8765/docs
    filinglens eval --mode rules            # score answers on evals/questions.yaml

Other companies: `filinglens companies --search "hcl tech"`, then `--add HCLTECH`, then
download and ingest. A question about a listed company that is not loaded is answered with
"not loaded" (and how to add it), never with another company's numbers.

## Asking questions
`filinglens ask` works with or without an LLM. Every path runs the same tools on the same data:
- LLM configured: the model picks the tool and writes a checked answer.
- No LLM, `--no-llm`, or the LLM fails (down, rate-limited, no valid call): a rule-based
  planner (`nlu/rules.py`: companies, metrics, years, scope, question type from registry.yaml)
  plans simple questions; the result is the table without a written summary.
- No metric recognised: the most relevant report passages, with page sources.
The pipeline lives in `service.py` (`AnswerService.answer`), shared by the CLI and the API.

## Asking questions (LLM)
    cp .env.example .env            # set FILINGLENS_LLM_BASE_URL / _API_KEY / _MODEL
    uv sync --extra llm
    filinglens llm-check            # connection + a few example questions
    filinglens ask "TCS ka munafa pichle 2 saal"
    filinglens ask "Teeno companies ka net margin FY24"
    filinglens ask --show-prompt    # the exact system prompt sent to the model

The model only chooses a tool (get_value, get_trend, compare, search_reports, ask_user) and its
arguments; every number comes from the database. A second call writes a short answer from the tool result:
text answers must cite retrieved pages ([p.44]) and every number in them must appear in the
passages or table (rounding allowed); otherwise the answer is dropped and the data is shown.
Companies in a tool call must be named in the question (stops "Wipro" becoming Infosys).
Flags: --evidence (show passages), --no-answer, --json. Search embeds the query on CPU when
the GPU is held by a local LLM (FILINGLENS_EMBED_DEVICE=auto|cpu|cuda). Arguments are validated against
registry.yaml; one correction round, then the user is asked to rephrase. Examples shown to the
model live in `src/filingLens/llm/examples.yaml`: add one when a kind of question goes wrong.

## Any listed company, latest data
    filinglens companies --search wipro          # NSE's list of all listed equities
    filinglens available TCS WIPRO               # what NSE has: years, quarters, reports (have/new)
    filinglens download WIPRO --scope consolidated   # unknown symbols are added automatically
    filinglens ingest work/inbox && filinglens status

Sources: annual + quarterly results (older listing) and SEBI Integrated Filing (Dec 2024 to the
latest quarter; the March quarter also carries the full year). Added companies live in
work/companies.yaml next to the built-in registry. Banks/NBFCs use a different taxonomy, so
most of the 14 metrics will be missing for them.

## Getting data and analysing it
    filinglens download TCS INFY HINDUNILVR --scope consolidated   # NSE XBRL -> work/inbox
    filinglens ingest work/inbox
    filinglens trend TCS revenue_from_operations          # values, YoY, CAGR, page citations
    filinglens trend INFY net_margin                       # derived ratio; change in pp
    filinglens compare net_margin --ticker TCS --ticker INFY --ticker HINDUNILVR
    filinglens compare net_profit --ticker TCS --ticker INFY --normalise index100
    filinglens compare revenue_from_operations net_profit --ticker TCS

Derived metrics: net_margin, pbt_margin, effective_tax_rate, employee_cost_ratio. Missing years
are shown as gaps (never interpolated); growth from a zero/negative base and CAGR of ratios are
not computed; crore, per-share and % values are never mixed in one table. NSE's FY22 "Annual"
XBRL files hold only segment breakdowns, so headline data currently starts at FY23.

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
    filinglens ingest work/inbox                             # XBRL -> PDFs -> calibration
    filinglens inspect-pdf report.pdf [--page 91]            # no database: sections, chunks

PDFs are read with PyMuPDF. Two-page spreads are split into halves ("panels") so each
half gets its own section/scope from its running header. Tables are not extracted
(they are garbled on spreads); statement rows survive as text. Each FY/INSTANT fact is
matched to the page that prints it; an FY25 report also verifies FY24 facts (prior-year
column). TCS FY24: 14/14 facts verified, all on PDF page 91 (printed pp. 178-179).

## Parsing an XBRL filing
    filinglens inspect path/to/TCS_Annual_INDAS_....xml        # no database; prints facts, checks, audit
    filinglens inspect path/to/file.xml --json                 # same, machine-readable
    filinglens ingest-xbrl work/inbox/TCS/xbrl               # files or folders

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

## HTTP API

```bash
uv sync --extra api
filinglens serve                 # http://127.0.0.1:8765/docs  (--no-llm, --port, --lazy)
```

| endpoint | returns |
|---|---|
| `POST /ask` `{"question": "...", "use_llm": true}` | written answer + data view |
| `GET /value?ticker=TCS&metric=revenue&fy=FY24` | one figure |
| `GET /trend?ticker=TCS&metric=net_margin&last_n_years=3` | series with YoY |
| `GET /compare?tickers=TCS&tickers=INFY&metrics=revenue_from_operations` | side by side |
| `GET /search?q=attrition&tickers=TCS` | report passages with pages |
| `GET /companies`, `/metrics`, `/health` | what is loaded |

Every data response is a typed `DataView` (`src/filingLens/views.py`), ready for a frontend:
`table` (typed columns + raw rows + formatted `display` rows), `chart` (suggested type and one
series of `(period, value)` points per line, `null` = gap), `stats` (value, change, CAGR),
`passages` and `sources`. Amounts are INR crore, changes in % (ratios: percentage points).
Generate frontend types from `/openapi.json`. `filinglens ask --json` prints the same view.
Allowed browser origins: `FILINGLENS_CORS` (default localhost:3000 and :5173).

### Accounts and roles

Every data endpoint needs a signed-in user; `/health`, the auth endpoints and the web UI's files
are public. Accounts are handled by [fastapi-users](https://fastapi-users.github.io/fastapi-users/)
and stored in Postgres (tables `user` and `access_token`). Passwords are
argon2-hashed; the session is an HttpOnly, SameSite=Lax cookie kept in the database for 7 days,
so signing out ends it on the server.

| endpoint | does |
|---|---|
| `POST /auth/register` `{"email", "password"}` | open sign-up (8+ characters); new accounts are `user` |
| `POST /auth/login` form `username=<email>&password=...` | sets the session cookie (204) |
| `POST /auth/logout` | ends the session |
| `GET /auth/me` | the signed-in account and its `role` |

Two roles, `admin` and `user`. Sign-up can't choose one; make an admin from the command line
(creates the account, asking for a password, or promotes an existing one):

```bash
filinglens users create-admin you@example.com
```

Routes are restricted with `Depends(auth.require_role("admin"))` (`src/filingLens/api/auth.py`).

Admin only (`src/filingLens/api/admin.py`; in the web UI: **Admin → Data**):

| endpoint | returns |
|---|---|
| `GET /admin/companies` | every NSE-listed equity (from `work/nse_equity_list.csv`) with what is held here: tracked or not, ingested XBRL files and their years, latest quarter, annual reports, report passages, files in the S3 bucket, inbox files not ingested yet |
| `GET /admin/companies/{symbol}/nse` | what NSE has for one company now (XBRL results and annual reports, last 5 years), each marked have / new; about 10 s, rate-limited, cached for an hour (`?refresh=true` to re-check) |
| `POST /admin/companies/{symbol}/download` | queue a download: every filing NSE has that isn't held → S3 bucket → ingest (adds an untracked company first) |
| `GET /admin/companies/{symbol}/download`, `GET /admin/jobs` | progress of downloads (this server session) |
| `GET /admin/companies/{symbol}/files` | the company's files in the bucket, with 15-minute download links |

The list is local and quick; NSE is asked one request at a time, so downloads run one company
after another. After a download the company can be asked about at once (figures and keyword
search); run `filinglens index` for semantic search (it uses the GPU, which llama-server shares).
Files are kept at `s3://filinglens/filings/<TICKER>/<xbrl|annual_report>/`; `work/inbox` is the
local working copy the parsers read. Refresh the equity list with `filinglens companies --refresh`. Settings: `FILINGLENS_COOKIE_SECURE=1` when served over HTTPS;
`FILINGLENS_AUTH_SECRET` signs reset/verification tokens (not used yet).

## Web UI

```bash
cd web && npm install
npm run dev                      # http://localhost:5173 (proxies the API on :8765 under /api)
npm run build                    # web/dist; `filinglens serve` then serves the UI at http://127.0.0.1:8765/
```

React + Ant Design 6 + ECharts (`web/`), dark theme by default. Signed out, it shows a home
page with sign-in and sign-up; signed in, the header has the account menu (email, role, sign out). **Ask** is a research thread: each answer is a card with the written
summary, headline tiles (total change, CAGR), a chart, the sortable table and every source with
its page. The chart type follows the shape of the data, and a switcher offers every form that fits:

| answer | views |
|---|---|
| one figure | large value, its history (sparkline), rank among covered peers |
| trend | line, bars, YoY change bars, bridge (waterfall of yearly changes) |
| quarterly trend | line with gaps where no figure was filed |
| comparison over years | grouped bars, line, indexed (first year = 100), slope, growth heatmap |
| comparison in one year | ranked bars, share donut |
| text question | report passages with page, section, query words highlighted |

**Explore** is a dashboard over all loaded companies: revenue/profit/margin cards, indexed growth,
ratio radar, revenue-to-net-profit bridge, share of the group, ratios over time and a YoY growth
heatmap. Companies keep one colour everywhere; dark, light or system theme (toggle in the header);
missing values stay blank. Link parameters: `?q=<question>` asks on load, `?theme=dark|light|system`.

`.mcp.json` registers an Ant Design docs MCP server (`@jzone-mcp/antd-components-mcp`, community)
so Claude Code can look up antd 6 component APIs when editing the UI.

## Dev
    docker compose up -d postgres                # tests need Postgres
    uv sync --extra dev --extra api --extra llm
    pytest                                       # each test gets an empty schema in database
                                                 # filinglens_test (FILINGLENS_TEST_DATABASE_URL)

## Evaluation
    filinglens eval --mode rules                 # seconds; rule-based planner + search
    filinglens eval --mode llm --limit 20        # real model (mind provider rate limits)
    filinglens eval --tag compare --tag hinglish # subsets by tag, or --id c03
`evals/questions.yaml` holds ~60 questions (values, quarters, trends, ratios, comparisons,
text, refusals, missing data, Hinglish, tricky wording) with expected tool, arguments, values
and pages. Each run prints a scorecard (right tool / arguments / values / pages, correct
refusals, no number when data is missing, fabricated numbers - must be 0), compares it with
the previous run, and saves the details to work/eval/. Update expected values when the data
changes, and keep the questions different from the prompt examples in llm/examples.yaml.
