"""Storage-layer exceptions with stable failure categories."""


class StorageError(RuntimeError):
    """Base exception for local persistence failures."""


class IntegrityError(StorageError):
    """Persisted bytes do not match their declared content hash or schema."""


class ImmutableManifestError(StorageError):
    """An immutable manifest path already contains different content."""


class LockUnavailableError(StorageError):
    """A logical run is already owned by another local process."""
