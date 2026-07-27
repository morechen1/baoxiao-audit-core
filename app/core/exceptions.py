class BaoxiaoError(Exception):
    """Base application error."""


class ValidationError(BaoxiaoError):
    """Raised for deterministic validation failures."""


class UnsafePathError(BaoxiaoError):
    """Raised when a path escapes its allowed root."""
