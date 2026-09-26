"""hub.metrics -- StructuralMetric operations.

A flexible name/value relation (not fixed CircuitVersion columns) so
new metrics never require a schema migration; see
qch.models.StructuralMetric.
"""

from __future__ import annotations

from typing import Any

from qch.models import StructuralMetric
from qch.repositories.interfaces import Storage


class MetricsService:
    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def record(
        self,
        version_id: str,
        metric_name: str,
        metric_value: float,
        *,
        metric_unit: str | None = None,
        computation_method: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StructuralMetric:
        """Natural key: (version_id, metric_name) -- an upsert, never a
        duplicate row."""
        return self._storage.record_metric(
            version_id, metric_name, metric_value, metric_unit=metric_unit, computation_method=computation_method, metadata=metadata
        )

    def get(self, version_id: str, metric_name: str) -> StructuralMetric | None:
        return self._storage.get_metric(version_id, metric_name)

    def list(self, version_id: str) -> list[StructuralMetric]:
        return self._storage.list_metrics(version_id)
