"""Domain exceptions. Each carries the HTTP status it maps to so the API layer stays thin."""


class GeoAPIError(Exception):
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFoundError(GeoAPIError):
    status_code = 404


class UnsupportedFileTypeError(GeoAPIError):
    status_code = 415


class FileTooLargeError(GeoAPIError):
    status_code = 413


class InvalidUploadError(GeoAPIError):
    """The upload itself is unusable (e.g. empty, no filename)."""

    status_code = 400


class FileNotReadyError(GeoAPIError):
    """The file exists but did not finish processing successfully."""

    status_code = 409


class GeoFileParseError(GeoAPIError):
    """The uploaded file could not be parsed as the declared geospatial format."""

    status_code = 422
