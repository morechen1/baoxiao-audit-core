class BaoxiaoError(Exception):
    """Base application error."""


class ValidationError(BaoxiaoError):
    """Raised for deterministic validation failures."""


class UnsafePathError(BaoxiaoError):
    """Raised when a path escapes its allowed root."""


class InvalidStateTransition(BaoxiaoError):
    """Raised when a record attempts an illegal state transition."""


class TrustGateError(BaoxiaoError):
    """Raised when data does not meet trusted-index requirements."""


class StructuredRecordError(BaoxiaoError):
    """Raised when a structured draft is invalid."""


class ReviewDecisionError(BaoxiaoError):
    """Raised when an auditable review decision cannot be applied."""


class UnsafeUrlError(BaoxiaoError):
    """Raised when a URL can reach a prohibited network target."""


class RawArtifactIntegrityError(BaoxiaoError):
    """Raised when an immutable stored source artifact cannot be verified."""


class CollectionError(BaoxiaoError):
    """Raised when collected content violates provenance or type constraints."""
