# QCH Phase 2D.1: Canonical Version Identity + Arbitrary Version Lookup

## 1. The problem this phase fixes

Phase 2D's own local acceptance testing surfaced two real defects:

1. **Wrong primary identity shown.** "Which version has the lowest
   score?" answered with the milestone label "V5" alone -- never the
   real, canonical identifier every version actually has
   (`ecdsafail:a39e07e`). A curated milestone label is a convenience
   alias for 5 of 781 real versions; it was being shown INSTEAD of the
   real identity rather than alongside it.
2. **No general way to ask about an arbitrary real version.** Only
   `version_id` (opaque internal UUID-hex), a full
   `ecdsafail:<7-hex>` key, or one of the 5 milestone labels could be
   used to name a version. A user typing a short form of a real
   identifier (e.g. `1196b9f` instead of `ecdsafail:1196b9f`), or the
   real LLM occasionally mis-planning a real identifier, could produce
   a false `UNKNOWN_ENTITY` for a version that genuinely exists.

## 2. Audit performed before writing any code

Read (not assumed) from source and the real `data/qch_ecdsa_p17.sqlite3`
snapshot:

- `qch.models.CircuitVersion` has three identity fields:
  `version_id` (opaque, internal), `external_version_key` (ALWAYS
  present, unique, format `"ecdsafail:<7-hex-char-git-short-sha>"` --
  verified: all 781 real versions conform), and `version_label`
  (OPTIONAL, set ONLY for the 5 frozen milestones V1-V5).
- `qch.query.resolve.resolve_version()` (used by every query operator)
  already tries all three, EXACT match only -- confirmed by reading
  `storage/sqlite/backend.py`'s
  `find_circuit_version_by_external_key` (`WHERE external_version_key
  = ?`, no `LIKE`/prefix support).
- The root cause of defect 1: `qch.nl.answer._fmt_version()` returned
  `label or external_key or version_id` -- i.e. it PREFERRED the
  milestone label whenever one existed.
- `docs/qch_query_schema.json` already listed a `version_metadata_fields`
  array (matching `qch.nl.validator`'s internal
  `_VERSION_METADATA_FIELDS` acceptance set) -- so `external_version_key`
  was ALREADY a legal `filter`/`filter_versions`/`get_metric`/`sort`
  field name as far as the validator and every query operator were
  concerned. It was simply never parsed into `SchemaContext` nor
  rendered into the LLM prompt, so the model never knew it existed as
  a general lookup key. This is the reason this phase needed **no new
  operator, no new metric, and no new schema field** -- only wiring
  already-legal, already-existing data through to the places that had
  never been told about it.
- Real facts gathered directly from the database (never hard-coded
  into any resolution rule -- see section 9): 781 total versions, 0
  malformed `external_version_key` values, 781/781 globally unique
  7-char hex suffixes (no short-ID collisions exist in this real
  snapshot today), V1..V5 map to `ecdsafail:6f7c159`,
  `ecdsafail:d19dbb5`, `ecdsafail:cddd5df`, `ecdsafail:422f21d`,
  `ecdsafail:a39e07e` respectively, and `b6f2b0a` matches zero real
  versions.

## 3. The canonical Version ID contract

- **PRIMARY identity**: `external_version_key` (e.g.
  `"ecdsafail:6f7c159"`). Always present, always unique, always the
  right thing to show a user or feed back into another query.
- **SECONDARY, optional alias**: `version_label` (e.g. `"V5"`). Only
  ever set for 5 of 781 real versions. Displayed alongside the
  canonical id, never instead of it.
- `version_id` remains an internal, opaque fallback (used only if
  `external_version_key` is somehow absent, which does not happen in
  the real dataset today).

## 4. The deterministic resolver

New module: `src/qch/nl/version_resolver.py`. `resolve_version_identifier(hub, raw_identifier)`
returns a `VersionResolution` with one of three outcomes:

| Outcome | Meaning |
|---|---|
| `RESOLVED` | Exactly one real version matches; `canonical_version_id` is its real `external_version_key`. |
| `UNKNOWN` | Genuinely no real version matches -- never guessed. |
| `AMBIGUOUS` | More than one real version's `external_version_key` shares this short form; real `candidates` are returned so a caller can ask, never silently pick one. |

Three tiers, in order:

1. **Exact match** -- reuses `qch.query.resolve.resolve_version()`
   UNCHANGED (version_id / external_version_key / version_label).
2. **Case-normalized exact retry** -- a user's capitalization should
   not matter for a real-world string identifier.
3. **Short-ID scan** (NEW, additive) -- an in-memory scan of every
   real version's `external_version_key` hex suffix, DB-grounded (not
   string-prepending): a token is accepted only if it is hex-shaped
   *and* contains at least one `a`-`f` letter (so a plain decimal
   metric threshold like `950000` is never mistaken for a version
   reference), and it must be an exact match or a real prefix of a
   real suffix. Zero, one, or more than one match determines
   `UNKNOWN`/`RESOLVED`/`AMBIGUOUS`.

No LLM call, no Git, no artifact extraction; only the already-open
`hub`'s existing `versions`/`circuits` services and a scan over the
current dataset's 781 rows (cheap, all in local SQLite memory).

## 5. Where canonicalization happens (the one boundary)

`qch.nl.version_resolver.canonicalize_plan_versions(hub, plan)` runs
inside `QCHNaturalLanguageService._answer_from_plan_ready`, in exactly
one place:

```
planner.plan() -> SemanticGuard.check() -> [repair + revalidate]
    -> canonicalize_plan_versions()   <-- NEW, Phase 2D.1
    -> QCHQueryExecutor.execute()
```

It runs **after** `SemanticGuard`, not before, and this is
deliberate: `SemanticGuard`'s traversal-direction rule
(`expected_branch_traversal_direction`, Phase 2C.1) regex-searches the
question for the plan's literal `version` parameter text. Canonicalizing
first would silently break that rule for any question phrased with a
milestone label or a short ID (the literal text in the plan would no
longer appear in the question). Running canonicalization after
`SemanticGuard` means that frozen module needed **zero changes** --
exactly as the spec required.

It rewrites, per step: `version`/`version_a`/`version_b` params
(`get_metric`, `traverse`, `compare_versions`), and any
`filter`/`filter_versions` condition naming `version_id` /
`external_version_key` / `version_label` with `==`/`!=` -- these are
all normalized onto `{"metric": "external_version_key", "value": <canonical>}`,
since that is always the one real stable identity regardless of which
identity field a plan happened to name. A plan with no version
reference at all comes back as the exact same object (not merely an
equal one), so `qch.nl.answer.build_explanation`'s existing "no
redundant duplicate when nothing was repaired" logic is unaffected.

- If a version param does not resolve: the service returns
  `SystemStatus.UNKNOWN_ENTITY` immediately (never executes the plan).
- If a version param resolves ambiguously: the service returns
  `SystemStatus.NEEDS_CLARIFICATION`, with the REAL candidate
  `external_version_key` values as `clarification_options`. Picking
  one substitutes it directly into the original question text (see
  `ConversationContext.ambiguous_raw_identifier` /
  `_substitute_identifier` in `service.py`) rather than appending, so
  the merged follow-up question makes sense.

## 6. MISSING_DATA vs. UNKNOWN_ENTITY vs. AMBIGUOUS

These were already three different things in the query engine; this
phase makes the distinction reliable for version identity specifically:

- **UNKNOWN_ENTITY**: the identifier does not resolve to any real
  version at all (e.g. `b6f2b0a`).
- **MISSING_DATA**: the identifier resolves to a real version, but
  that version lacks the requested metric (e.g.
  `ecdsafail:c55911f`'s structural Toffoli count -- a real,
  non-milestone version with no structural extraction yet).
- **NEEDS_CLARIFICATION (AMBIGUOUS)**: the identifier is a short form
  that matches more than one real version.

## 7. Grounding the LLM (fixing false UNKNOWN_ENTITY)

Two additive, non-breaking prompt changes (`src/qch/nl/prompts.py`,
version bumped `qch-plan-zero-v1`/`qch-plan-fewshot-v1` ->
`-v2` per this project's own prompt-versioning discipline):

1. A new "VERSION_METADATA_FIELDS" section in the rendered schema
   block, teaching the model that `external_version_key` is a
   version's primary, stable identity and is legal in
   filter/get_metric/sort conditions -- previously-dormant schema data
   (`docs/qch_query_schema.json`'s `version_metadata_fields`, now
   parsed by `qch.nl.schema_context.SchemaContext`) simply exposed.
2. A new "KNOWN VERSION IDENTIFIERS" context line: before planning,
   `QCHNaturalLanguageService` extracts identifier-shaped tokens from
   the raw question (`extract_candidate_version_identifiers`) and
   resolves each one against the real store; any that resolve are
   handed to the planner as already-confirmed-real facts (`question
   text 'V5' -> external_version_key 'ecdsafail:a39e07e'`), so the
   model does not have to guess whether a token is real.

This directly reduces (never expands) the LLM's role in existence
checking: the deterministic resolver decides what is real; the model
is only ever told the answer, and the post-planning canonicalization
boundary (section 5) is the authoritative backstop regardless of what
the model assumed.

## 8. Answer rendering (canonical ID primary everywhere)

`qch.nl.answer._fmt_version()` is the single function every answer
shape (scalar `get_metric`, `compare_versions`, transition
parent/child, plain version list) already funneled through. It now
returns `"{canonical} (milestone {label})"` when both exist,
`canonical` alone otherwise, and `label` alone only as a last-resort
fallback (never reached for any real version in the current dataset,
since `external_version_key` is always present). No answer-shape
branch needed its own fix; fixing this one function fixed all of them.

## 9. Anti-hardcoding

No rule in this phase names a specific version, question, or value.
`resolve_version_identifier`/`canonicalize_plan_versions` operate
purely generically over whatever `hub.versions.list(...)` returns at
call time. The real identifiers used in this document and in tests
(`ecdsafail:a39e07e`, `b6f2b0a`, `ecdsafail:c55911f`, ...) are RESULTS
of running the resolver against the real database, recorded here for
verification -- never inputs to a conditional anywhere in
`version_resolver.py` or `service.py`.

## 10. What was deliberately NOT changed

- `qch.nl.semantic_guard` -- zero changes (see section 5's rationale).
- Every query operator, `QCHQueryExecutor`, `qch.query.resolve.resolve_version`,
  `qch.storage.sqlite.backend` -- zero changes. Short-ID resolution is
  purely additive, living entirely in the new `qch.nl.version_resolver`
  module.
- No new query operator, no new metric, no new relation type.
- `pages/Ask_QCH.py` -- zero changes. The page already renders
  whatever `result.answer`/`result.explanation` contain verbatim, so
  the canonical-ID fix and the new UNKNOWN_ENTITY/AMBIGUOUS paths flow
  through automatically.

One incidental, pre-existing bug this phase's own prompt-version bump
exposed and fixed: `src/ask_qch_config.py` had the fewshot prompt
version HARD-CODED as a literal string (`"qch-plan-fewshot-v1"`)
instead of importing `qch.nl.prompts.PROMPT_VERSION_FEWSHOT`. Bumping
the constant without also fixing this would have made the live Ask QCH
page raise `ValueError: unknown prompt_version` on every question.
Fixed by importing the constant instead of duplicating its value.

## 11. Observability

`VersionResolution.to_dict()` (and `QueryExplanation.version_resolutions`,
new field, surfaced automatically in the existing "Show Provenance"
panel with no UI change) records, per resolved reference:
`raw_identifier`, `normalized_identifier`, `canonical_version_id`,
`alias_used` (the milestone label, if any), `resolution_status`
(`exact_match` / `exact_match_case_normalized` / `short_id_match` /
`ambiguous_short_id` / `unknown` / `empty`), and `candidates` (for
`AMBIGUOUS` only).

## 12. Testing

- **Unit** (`tests/test_qch_version_resolver.py`, 22 tests, no LLM, no
  real snapshot, a small in-memory hub): exact match on all three
  identity fields, case-insensitivity, unique short-ID resolution,
  short-ID collision -> AMBIGUOUS with real candidates, genuine
  non-existence, a pure-decimal token never mistaken for a short ID,
  empty/non-string input, candidate-token extraction, and full
  plan-canonicalization coverage (`get_metric`/`compare_versions`/
  `traverse`/`filter_versions` conditions, UNKNOWN_ENTITY/AMBIGUOUS
  short-circuiting, and the no-op/identity-preserving case).
- **Real-database** (`tests/test_qch_nl_service_real_db_integration.py`,
  6 new tests): the exact "lowest score" acceptance criterion (answer
  contains the real canonical id, not just the bare label);
  `b6f2b0a`'s real non-existence; all 5 sampled real, non-milestone
  short IDs resolve to their real full keys; a global uniqueness
  regression guard over all 781 real `external_version_key` suffixes;
  MISSING_DATA (not UNKNOWN_ENTITY) for a real version lacking
  structural data.
- **Real NL acceptance run** (real `qwen2.5:14b-instruct-q4_K_M`, real
  `data/qch_ecdsa_p17.sqlite3`, run once):
  `scripts/phase2d1_version_identity_acceptance_run.py` ->
  `results/phase2d1/version_identity_acceptance_run_2026-09-23.json`
  (see the completion report for the question-by-question results).
- **Full regression**: the entire existing test suite (628 tests
  outside the one slow real-Git-history test, plus that test itself)
  was re-run after every change in this phase; see the completion
  report for the exact pass count.
