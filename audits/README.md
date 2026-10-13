# Read-only data audits

These scripts are **deliberately backend-specific**: they open the SQLite store
with `mode=ro` and use hand-written SQL so that their evidence is independent of
the QCH Query Engine, services and importers they are auditing.

That is why they live here and not in `scripts/`. `tests/test_qch_storage_independence.py`
requires every `scripts/*.py` CLI to stay backend-agnostic (no `sqlite3`, no SQL
text), and these audits must not weaken that rule.

Rules for this directory:
- never write the database (each script prints the DB SHA-256 before and after, and fails if it changed);
- never download data or run downloaded code;
- outputs go to `results/qch_sql_audit/` (git-ignored).

| Script | Report |
|---|---|
| `qch_sql_q1_q20_audit.py` | `docs/QCH_SQL_Q1_Q20_DATA_AUDIT.md` |
| `qch_sql_research_supplement_audit.py` | `docs/QCH_SQL_MISSING_DATA_SOURCE_AUDIT.md` |
| `qch_sql_supplement_import_validation.py` | `docs/QCH_SQL_SUPPLEMENT_IMPORT_REPORT.md` |
