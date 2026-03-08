class CliError(Exception):
    """User-facing error with terse actionable text."""


class ApiError(CliError):
    """Google API/auth failure with actionable text."""
