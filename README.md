# India Equity Research Copilot

Facts come from a verified database (XBRL), never from an LLM. See the V1 build plan.

## Status
- [x] Phase 1: `domain` (fiscal, units, models, registry.yaml)
- [x] Phase 2: `storage` (schema, db, FactRepo, ValidationRepo)
- [ ] Phase 3: ingest/xbrl + validate  (needs Phase 0: diagnose missing FY facts in the notebook)

## Dev
    pip install -e ".[dev]"
    pytest
