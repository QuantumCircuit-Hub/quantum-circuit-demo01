"""QCH: a general-purpose, storage-independent Quantum Circuit Hub
database core.

    from qch import QCH

    hub = QCH.open("my-qch.sqlite3")
    hub.circuits.create("qch:logical:my_circuit", name="my_circuit")

See src/qch/hub.py for the full public surface, and
docs/DB1_ARCHITECTURE.md for the design this package implements. This
is not specific to any one dataset -- src/qch/importers/ecdsafail.py is
the first of what should eventually be several dataset importers built
on the same domain API.
"""

from qch.exceptions import DuplicateError, NotFoundError, QCHError, StorageError, ValidationError
from qch.hub import QCH

__all__ = [
    "QCH",
    "QCHError",
    "NotFoundError",
    "DuplicateError",
    "ValidationError",
    "StorageError",
]
