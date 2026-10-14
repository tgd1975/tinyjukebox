"""Errors of the box control (see :mod:`tinyjukebox.control`); the rest api maps them to http status codes."""

from typing import Any

__all__ = [
    "ApiError",
    "BadRequest",
    "Conflict",
    "Forbidden",
    "Gone",
    "InsufficientStorage",
    "LengthRequired",
    "MethodNotAllowed",
    "NotFound",
    "PayloadTooLarge",
    "ServiceUnavailable",
    "TooManyRequests",
    "Unauthorized",
    "Unprocessable",
]


class ApiError(Exception):
    """Base class - ``message`` is the English text of the error, ``details`` are added to the json error response.

    An error that a user of the web page runs into has a ``code`` among its details, and the values its text needs
    (``Conflict("tag ... exists already.", code="tag.exists", uid=uid)``): the page shows its own text for the code,
    in its language (``error.<code>`` in ``web/web/texts``), and the message for an error without one."""

    status = 500

    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


# the names follow the http status texts - an 'Error' suffix would only add noise
class BadRequest(ApiError):  # noqa: N818
    status = 400


class Unauthorized(ApiError):  # noqa: N818
    status = 401


class Forbidden(ApiError):  # noqa: N818
    status = 403


class NotFound(ApiError):  # noqa: N818
    status = 404


class MethodNotAllowed(ApiError):  # noqa: N818
    status = 405

    def __init__(self, allowed: list[str]) -> None:
        super().__init__("method not allowed.", allowed=sorted(set(allowed)))


class Conflict(ApiError):  # noqa: N818
    status = 409


class Gone(ApiError):  # noqa: N818
    status = 410


class LengthRequired(ApiError):  # noqa: N818
    status = 411


class PayloadTooLarge(ApiError):  # noqa: N818
    status = 413


class Unprocessable(ApiError):  # noqa: N818
    status = 422


class TooManyRequests(ApiError):  # noqa: N818
    status = 429


class ServiceUnavailable(ApiError):  # noqa: N818
    status = 503


class InsufficientStorage(ApiError):  # noqa: N818
    status = 507
