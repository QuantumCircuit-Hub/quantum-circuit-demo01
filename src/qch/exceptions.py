"""Domain-level exceptions for the QCH database API.

Callers using `qch.QCH` (the public facade) should only ever need to
catch these -- never a backend-specific exception such as
`sqlite3.IntegrityError`. See src/qch/storage/sqlite/backend.py for
where backend errors are translated into these.
"""

from __future__ import annotations


class QCHError(Exception):
    """Base class for all QCH domain errors."""


class NotFoundError(QCHError):
    """Raised when a requested entity does not exist."""


class DuplicateError(QCHError):
    """Raised when an operation would create a duplicate of an entity
    that is expected to be unique (e.g. a LogicalCircuit ID, or a
    (repository, commit_sha) pair)."""


class ValidationError(QCHError):
    """Raised when caller-supplied data violates a domain rule (e.g. a
    VerificationResult naming more than one target, or none)."""


class StorageError(QCHError):
    """Raised for backend failures that are not one of the more specific
    errors above (e.g. an unexpected constraint violation, a connection
    failure). Wraps the original backend exception as `__cause__`."""
