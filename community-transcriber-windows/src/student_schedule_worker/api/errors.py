from __future__ import annotations


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class UnauthorizedError(ApiError):
    pass


class LeaseLostError(ApiError):
    pass


class ProtocolError(ApiError):
    pass


class MediaIntegrityError(ProtocolError):
    pass


class OperationCancelled(RuntimeError):
    pass
