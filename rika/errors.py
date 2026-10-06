"""Typed errors.

Each error carries a technical message (for rika.log) and a ``line_key`` that
picks a short, friendly spoken line from :mod:`rika.lines`.
"""
from __future__ import annotations


class RikaError(Exception):
    line_key = "error_generic"

    def __init__(self, message: str, **fmt: object) -> None:
        super().__init__(message)
        self.fmt = fmt


class Cancelled(Exception):
    """Raised when the user says stop/cancel or the action is interrupted."""


class NoMicrophone(RikaError):
    line_key = "error_no_mic"


class NoInternet(RikaError):
    line_key = "error_no_internet"


class GmailNotConfigured(RikaError):
    line_key = "error_gmail_not_configured"


class GmailTokenExpired(RikaError):
    line_key = "error_token_expired"


class GmailError(RikaError):
    line_key = "error_gmail"


class AccountNotFound(RikaError):
    line_key = "error_account_not_found"


class ChromeNotFound(RikaError):
    line_key = "error_chrome_not_found"


class PrinterNotFound(RikaError):
    line_key = "error_printer_not_found"


class PrinterOffline(RikaError):
    line_key = "error_printer_offline"


class PrintFailed(RikaError):
    line_key = "error_print_failed"


class ConfigError(RikaError):
    line_key = "error_config"
