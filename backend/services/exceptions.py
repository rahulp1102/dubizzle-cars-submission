"""Domain exception hierarchy translated into structured HTTP responses by FastAPI."""

from __future__ import annotations


class AppError(Exception):
    """Base application error carrying an HTTP status code and machine-readable code."""

    def __init__(self, detail: str, *, status_code: int = 400, error_code: str = "app_error") -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.error_code = error_code


class NotFoundError(AppError):
    """Raised when a requested entity does not exist."""

    def __init__(self, detail: str, *, error_code: str = "not_found") -> None:
        super().__init__(detail, status_code=404, error_code=error_code)


class BadRequestError(AppError):
    """Raised for domain-level invalid input or state transitions."""

    def __init__(self, detail: str, *, error_code: str = "bad_request") -> None:
        super().__init__(detail, status_code=400, error_code=error_code)


class ConflictError(AppError):
    """Raised when an operation conflicts with persisted state (e.g. double booking)."""

    def __init__(self, detail: str, *, error_code: str = "conflict") -> None:
        super().__init__(detail, status_code=409, error_code=error_code)


class ForbiddenError(AppError):
    """Raised when a user attempts to read or modify another user's resource."""

    def __init__(self, detail: str, *, error_code: str = "forbidden") -> None:
        super().__init__(detail, status_code=403, error_code=error_code)


class UnauthorizedError(AppError):
    """Raised when required authentication credentials are missing."""

    def __init__(self, detail: str, *, error_code: str = "unauthorized") -> None:
        super().__init__(detail, status_code=401, error_code=error_code)
