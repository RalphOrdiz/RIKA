"""Gmail OAuth (installed-app flow), read-only scope.

credentials.json  -> %APPDATA%\\Rika\\credentials.json (or next to Rika.exe)
token.json        -> %APPDATA%\\Rika\\token.json
Rika never sees your Google password: Google's own login page handles it.
"""
from __future__ import annotations

import json
import logging
import threading

from rika import paths
from rika.errors import GmailNotConfigured, GmailTokenExpired, NoInternet

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_lock = threading.Lock()


def has_credentials_file() -> bool:
    return paths.credentials_path().exists()


def load_credentials():
    """Return valid Credentials, refreshing if needed. Never opens a browser."""
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    with _lock:
        token = paths.token_path()
        if not token.exists():
            if not has_credentials_file():
                raise GmailNotConfigured(f"credentials.json not found at {paths.credentials_path()}")
            raise GmailNotConfigured("Gmail is not connected yet (no token.json) - use 'Connect Gmail'")
        try:
            creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        except (ValueError, json.JSONDecodeError) as exc:
            raise GmailTokenExpired(f"token.json is unreadable: {exc}")
        if creds.valid:
            return creds
        if not creds.refresh_token:
            raise GmailTokenExpired("Gmail token has no refresh token - reconnect Gmail")
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise GmailTokenExpired(f"Gmail token expired or was revoked: {exc}")
        except TransportError as exc:
            raise NoInternet(f"Could not refresh Gmail token (network): {exc}")
        token.write_text(creds.to_json(), encoding="utf-8")
        return creds


def run_auth_flow(login_hint: str | None = None, timeout_seconds: int = 300):
    """Open Google's sign-in page in the browser and store token.json."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    cred_file = paths.credentials_path()
    if not cred_file.exists():
        raise GmailNotConfigured(f"credentials.json not found at {cred_file} - see SETUP.md")
    with _lock:
        flow = InstalledAppFlow.from_client_secrets_file(str(cred_file), SCOPES)
        kwargs = {"login_hint": login_hint} if login_hint else {}
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            timeout_seconds=timeout_seconds,
            authorization_prompt_message="Opening Google sign-in in your browser...",
            success_message="Rika is connected to Gmail! You can close this tab now~",
            prompt="consent",
            **kwargs,
        )
        if creds is None:
            raise GmailNotConfigured("Google sign-in was not completed in time")
        paths.token_path().write_text(creds.to_json(), encoding="utf-8")
        log.info("Saved Gmail token to %s", paths.token_path())
        return creds


def disconnect() -> None:
    try:
        paths.token_path().unlink()
        log.info("Removed token.json")
    except FileNotFoundError:
        pass
