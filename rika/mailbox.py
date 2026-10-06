"""Gmail API access: find attachments and download them (gmail.readonly)."""
from __future__ import annotations

import base64
import logging
import re
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from pathlib import Path
from typing import Callable, Protocol

from rika.config import ALLOWED_EXTENSIONS
from rika.errors import GmailError, GmailTokenExpired, NoInternet

log = logging.getLogger(__name__)


@dataclass
class Attachment:
    message_id: str
    attachment_id: str
    filename: str
    sender_name: str
    sender_email: str
    received: datetime
    size: int = 0
    subject: str = ""
    inline_data: str | None = None

    @property
    def ext(self) -> str:
        return Path(self.filename).suffix.lower()


class Mailbox(Protocol):
    def search_by_name(self, name: str) -> list[Attachment]: ...
    def recent_attachments(self, days: int, max_messages: int) -> list[Attachment]: ...
    def download(self, att: Attachment, dest_dir: Path) -> Path: ...


def _gmail_call(fn: Callable):
    """Run a Gmail API call, translating failures into Rika errors."""
    import httplib2
    from google.auth.exceptions import RefreshError, TransportError
    from googleapiclient.errors import HttpError

    for attempt in range(3):
        try:
            return fn()
        except RefreshError as exc:
            raise GmailTokenExpired(f"Gmail token expired or revoked: {exc}")
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            if status == 401:
                raise GmailTokenExpired(f"Gmail rejected the token (401): {exc}")
            if status in (429, 500, 503) and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise GmailError(f"Gmail API error {status}: {exc}")
        except (httplib2.ServerNotFoundError, TransportError, socket.gaierror, socket.timeout, ConnectionError) as exc:
            raise NoInternet(f"Cannot reach Gmail: {exc!r}")
        except OSError as exc:
            raise NoInternet(f"Network error talking to Gmail: {exc!r}")


class GmailMailbox:
    def __init__(self, account_email: str, credentials_loader: Callable):
        self.account_email = account_email
        self._load_creds = credentials_loader
        self._service = None
        self._cache: dict[str, list[Attachment]] = {}
        self._checked_profile = False

    # -------------------------------------------------------------- plumbing
    def connect(self) -> None:
        _ = self.service

    @property
    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            creds = self._load_creds()
            self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        if not self._checked_profile:
            self._checked_profile = True
            try:
                prof = _gmail_call(lambda: self._service.users().getProfile(userId="me").execute())
                actual = prof.get("emailAddress", "")
                if actual.lower() != self.account_email.lower():
                    log.warning("Gmail token belongs to %s but the print account is %s", actual, self.account_email)
                else:
                    log.info("Gmail connected as %s", actual)
            except GmailTokenExpired:
                self._checked_profile = False
                raise
        return self._service

    def _list_ids(self, query: str, limit: int) -> list[str]:
        ids: list[str] = []
        token = None
        while len(ids) < limit:
            req = self.service.users().messages().list(
                userId="me", q=query, maxResults=min(100, limit - len(ids)), pageToken=token
            )
            resp = _gmail_call(req.execute)
            ids += [m["id"] for m in resp.get("messages", [])]
            token = resp.get("nextPageToken")
            if not token:
                break
        log.info("Gmail query %r -> %d messages", query, len(ids))
        return ids

    def _fetch(self, ids: list[str]) -> list[Attachment]:
        missing = [i for i in ids if i not in self._cache]
        failed: list[str] = []

        def on_result(request_id, response, exception):
            if exception is not None:
                failed.append(request_id)
            else:
                self._cache[request_id] = _attachments_from_message(response)

        for start in range(0, len(missing), 25):
            chunk = missing[start : start + 25]
            batch = self.service.new_batch_http_request(callback=on_result)
            for mid in chunk:
                batch.add(
                    self.service.users().messages().get(
                        userId="me", id=mid, format="full", fields="id,internalDate,payload"
                    ),
                    request_id=mid,
                )
            _gmail_call(batch.execute)
        for mid in failed:  # retry one by one (rate limits)
            msg = _gmail_call(
                self.service.users().messages().get(userId="me", id=mid, format="full", fields="id,internalDate,payload").execute
            )
            self._cache[mid] = _attachments_from_message(msg)

        out: list[Attachment] = []
        for mid in ids:
            out += self._cache.get(mid, [])
        out.sort(key=lambda a: a.received, reverse=True)
        return out

    # -------------------------------------------------------------- public API
    def search_by_name(self, name: str) -> list[Attachment]:
        if Path(name).suffix.lower() in ALLOWED_EXTENSIONS:
            term = f'"{name}"' if " " in name else name
            query = f"has:attachment filename:{term}"
        else:
            term = f'"{name}"' if " " in name else name
            query = f"has:attachment filename:{term} {{filename:pdf filename:doc filename:docx}}"
        return self._fetch(self._list_ids(query, 20))

    def recent_attachments(self, days: int, max_messages: int) -> list[Attachment]:
        query = f"has:attachment newer_than:{int(days)}d {{filename:pdf filename:doc filename:docx}}"
        return self._fetch(self._list_ids(query, max_messages))

    def download(self, att: Attachment, dest_dir: Path) -> Path:
        if att.inline_data:
            data = att.inline_data
        else:
            resp = _gmail_call(
                self.service.users().messages().attachments().get(
                    userId="me", messageId=att.message_id, id=att.attachment_id
                ).execute
            )
            data = resp["data"]
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = dest_dir / f"{stamp}_{safe_filename(att.filename)}"
        path.write_bytes(raw)
        log.info("Downloaded %s (%d bytes) -> %s", att.filename, len(raw), path)
        return path


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name[:150] or "attachment"


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _attachments_from_message(msg: dict) -> list[Attachment]:
    payload = msg.get("payload", {})
    headers = payload.get("headers", [])
    sender_name, sender_email = parseaddr(_header(headers, "From"))
    sender_name = sender_name.strip().strip('"') or sender_email.split("@")[0]
    received = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000)
    subject = _header(headers, "Subject")
    out: list[Attachment] = []

    def walk(part: dict) -> None:
        fname = part.get("filename") or ""
        body = part.get("body", {})
        if fname and Path(fname).suffix.lower() in ALLOWED_EXTENSIONS:
            if body.get("attachmentId") or body.get("data"):
                out.append(
                    Attachment(
                        message_id=msg["id"],
                        attachment_id=body.get("attachmentId", ""),
                        filename=fname,
                        sender_name=sender_name,
                        sender_email=sender_email,
                        received=received,
                        size=int(body.get("size", 0)),
                        subject=subject,
                        inline_data=None if body.get("attachmentId") else body.get("data"),
                    )
                )
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return out
