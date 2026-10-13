-- QCH schema v7: population roles for research-supplement populations.
--
-- See docs/QCH_SQL_SUPPLEMENT_IMPORT_REPORT.md (correction addendum). v6
-- stored the eight published Table 2 Pareto points of the ECDSA.Fail paper
-- under a rule text that describes the Pareto computation's INPUT (candidate)
-- population. A candidate set and the frontier computed from it must never
-- share one identity, so each population now states its role, the
-- population it was derived from, and which source field (if any) its
-- rule_text quotes verbatim.
--
-- Additive only: four nullable/defaulted columns on research_population; no
-- existing row or table is modified here. The data correction itself is an
-- explicit, transactional importer step (not a migration), so a store that
-- never ran the research import is unaffected.

-- 'candidate_set': the input population of a published computation.
-- 'published_pareto_frontier': the nondominated points a source publishes
--   for a candidate_set (membership = the source's listed IDs).
-- NULL: a v6 row not yet corrected.
ALTER TABLE research_population ADD COLUMN population_role TEXT
    CHECK (population_role IS NULL OR population_role IN ('candidate_set', 'published_pareto_frontier'));

-- For a published frontier: the candidate_set it is the Pareto set of.
ALTER TABLE research_population ADD COLUMN derived_from_population_id TEXT
    REFERENCES research_population(population_id);

-- The source field that rule_text quotes VERBATIM; NULL when rule_text is a
-- QCH statement of the membership basis (e.g. "the IDs listed in ...").
ALTER TABLE research_population ADD COLUMN rule_source_field TEXT;

-- Further provenance: input files + hashes, verbatim source text kept for
-- reference (labelled with what it applies to), former names.
ALTER TABLE research_population ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}';
