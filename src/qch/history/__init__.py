"""QCH DB-5 Phase A11: turning the full ~781-version ECDSA.Fail
submission history into a real, queryable, honestly-labeled dataset.

Two concerns, two modules, both built only on the existing public
qch.QCH service surface (hub.versions/evolution/submissions/provenance/
materializations/structures/metrics) -- neither imports qch.storage or
constructs SQL:

- `version_graph.py`: reconstructs real parent/child structure among
  the ~781 eligible CircuitVersions that ECDSAFailHistoryImporter
  deliberately leaves disconnected (see that module's own docstring:
  "every historical_successor edge ... is deferred, not invented").
- `extraction.py`: a resumable pipeline that materializes a version's
  artifact and runs the existing structural analyzer on it, recording
  exactly what succeeded, what failed, and why -- never fabricating a
  metric for a version whose artifact could not be built or parsed.
"""
