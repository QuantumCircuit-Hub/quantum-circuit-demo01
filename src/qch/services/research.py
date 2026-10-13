"""QCH schema v6: research-supplement annotations -- pinned external research
sources (e.g. the ECDSA.Fail paper's supplemental materials), author-assigned
primary technique labels on CircuitVersions, and named submission-scoped
populations. Read and record only through the Storage interface."""

from __future__ import annotations

from qch.models import PopulationMembership, ResearchPopulation, ResearchSource, TechniqueLabel
from qch.repositories.interfaces import Storage


class ResearchSupplementsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def record_import(
        self,
        source: ResearchSource,
        labels: list[TechniqueLabel] | None = None,
        population: ResearchPopulation | None = None,
        memberships: list[PopulationMembership] | None = None,
    ) -> bool:
        """All-or-nothing; False (nothing written) when this exact source was
        already imported with identical rows; raises ValidationError on a
        conflicting re-import (see Storage.record_research_import)."""
        return self._storage.record_research_import(source, list(labels or []), population, list(memberships or []))

    def record_populations(
        self,
        source: ResearchSource,
        entries: list[tuple[ResearchPopulation, list[PopulationMembership]]],
        renames: dict[str, str] | None = None,
    ) -> bool:
        """All-or-nothing insert/correction of source-bound populations; False
        when nothing changed (see Storage.record_research_populations)."""
        return self._storage.record_research_populations(source, [(p, list(ms)) for p, ms in entries], dict(renames or {}))

    def list_populations(self) -> list[ResearchPopulation]:
        return self._storage.list_research_populations()

    def get_source(self, source_id: str) -> ResearchSource | None:
        return self._storage.get_research_source(source_id)

    def list_sources(self) -> list[ResearchSource]:
        return self._storage.list_research_sources()

    def list_technique_labels(self, source_id: str | None = None) -> list[TechniqueLabel]:
        return self._storage.list_technique_labels(source_id)

    def get_population(self, name: str) -> ResearchPopulation | None:
        return self._storage.get_research_population(name)

    def list_members(self, name: str) -> list[PopulationMembership]:
        population = self._storage.get_research_population(name)
        return [] if population is None else self._storage.list_population_memberships(population.population_id)
