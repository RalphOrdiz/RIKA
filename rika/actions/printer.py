"""Printing: printer lookup/status, page counting, SumatraPDF / Word / LibreOffice, print log."""
from __future__ import annotations

import csv
import logging
import os
import re
import shutil
import subprocess
import time
import zipfile
from datetime import datetime
from pathlib import Path

from rika import paths
from rika.errors import PrinterNotFound, PrinterOffline, PrintFailed

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x400
PRINTER_STATUS_OFFLINE = 0x80
PRINTER_STATUS_ERROR = 0x2
PRINTER_STATUS_PAPER_OUT = 0x10
PRINTER_STATUS_NOT_AVAILABLE = 0x1000


# ------------------------------------------------------------------ printers

def list_printers() -> list[str]:
    import win32print

    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def default_printer() -> str | None:
    import win32print

    try:
        return win32print.GetDefaultPrinter()
    except Exception:  # no default printer set
        return None


def resolve_printer(configured: str) -> tuple[str, bool]:
    """Return (printer name, uses_windows_default).

    Falls back to the Windows default printer when none is configured.
    Raises PrinterNotFound if the configured printer is not installed.
    """
    installed = list_printers()
    if configured:
        for name in installed:
            if name.lower() == configured.lower():
                return name, False
        raise PrinterNotFound(
            f"Printer '{configured}' is not installed. Installed printers: {installed or 'none'}",
            printer=short_printer_name(configured),
        )
    default = default_printer()
    if not default:
        raise PrinterNotFound("No printer configured and Windows has no default printer", printer="default")
    return default, True


def check_printer_ready(name: str) -> None:
    """Raise PrinterOffline if Windows reports the printer as offline."""
    import win32print

    try:
        handle = win32print.OpenPrinter(name)
    except Exception as exc:
        raise PrinterNotFound(f"Cannot open printer '{name}': {exc}", printer=short_printer_name(name))
    try:
        info = win32print.GetPrinter(handle, 2)
    finally:
        win32print.ClosePrinter(handle)
    status, attrs = info.get("Status", 0), info.get("Attributes", 0)
    log.info("Printer '%s' status=0x%x attributes=0x%x jobs=%s", name, status, attrs, info.get("cJobs"))
    if attrs & PRINTER_ATTRIBUTE_WORK_OFFLINE or status & (PRINTER_STATUS_OFFLINE | PRINTER_STATUS_NOT_AVAILABLE):
        raise PrinterOffline(f"Printer '{name}' is offline (status=0x{status:x}, attributes=0x{attrs:x})")
    if status & PRINTER_STATUS_PAPER_OUT:
        log.warning("Printer '%s' reports paper out", name)
    if status & PRINTER_STATUS_ERROR:
        log.warning("Printer '%s' reports an error state", name)


def short_printer_name(name: str) -> str:
    """'EPSON L120 Series' -> 'EPSON L120' (nicer to say out loud)."""
    name = re.sub(r"\s*\(.*?\)\s*", " ", name)
    name = re.sub(r"\s+series$", "", name.strip(), flags=re.I)
    return name.strip()


# ------------------------------------------------------------------ pages

def count_pages(path: Path) -> int | None:
    ext = path.suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader

            return len(PdfReader(str(path)).pages)
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("docProps/app.xml").decode("utf-8", "replace")
            m = re.search(r"<Pages>(\d+)</Pages>", xml)
            return int(m.group(1)) if m else None
    except Exception as exc:
        log.warning("Could not count pages of %s: %s", path.name, exc)
    return None


def pages_phrase(pages: int | None, ext: str) -> str:
    if pages is None:
        return ""
    approx = "about " if ext in (".docx", ".doc") else ""
    return f", {approx}{pages} page{'s' if pages != 1 else ''}"


# ------------------------------------------------------------------ PDF

def find_sumatra(configured: str = "") -> Path | None:
    candidates = []
    if configured:
        candidates.append(Path(os.path.expandvars(configured)))
    candidates += [
        paths.vendor_dir() / "SumatraPDF.exe",
        paths.app_dir() / "vendor" / "SumatraPDF.exe",
        paths.app_dir() / "SumatraPDF.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "SumatraPDF" / "SumatraPDF.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "SumatraPDF" / "SumatraPDF.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "SumatraPDF" / "SumatraPDF.exe",
    ]
    for c in candidates:
        if c.is_file():
            return c
    reg = _app_path_from_registry("SumatraPDF.exe")
    if reg:
        return reg
    found = shutil.which("SumatraPDF.exe")
    return Path(found) if found else None


def _app_path_from_registry(exe: str) -> Path | None:
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                val, _ = winreg.QueryValueEx(key, "")
                p = Path(val.strip('"'))
                if p.is_file():
                    return p
        except OSError:
            continue
    return None


def print_pdf(path: Path, printer: str, use_default: bool, sumatra_cfg: str = "") -> str:
    sumatra = find_sumatra(sumatra_cfg)
    if sumatra:
        target = ["-print-to-default"] if use_default else ["-print-to", printer]
        cmd = [str(sumatra), *target, "-silent", str(path)]
        log.info("Printing PDF: %s", subprocess.list2cmdline(cmd))
        try:
            proc = subprocess.run(cmd, timeout=180, creationflags=CREATE_NO_WINDOW, capture_output=True)
        except subprocess.TimeoutExpired:
            raise PrintFailed(f"SumatraPDF timed out printing {path.name}")
        if proc.returncode != 0:
            raise PrintFailed(f"SumatraPDF exit code {proc.returncode} printing {path.name}: {proc.stderr[:300]!r}")
        return "sent (SumatraPDF)"

    log.warning("SumatraPDF not found; falling back to the Windows 'printto' shell verb")
    import win32api

    try:
        win32api.ShellExecute(0, "printto", str(path), f'"{printer}"', str(path.parent), 0)
    except Exception as exc:
        raise PrintFailed(f"No SumatraPDF and shell printing failed for {path.name}: {exc}")
    return "sent (shell printto)"


# ------------------------------------------------------------------ Word

def print_word(path: Path, printer: str) -> str:
    try:
        return _print_with_word(path, printer)
    except _WordMissing as exc:
        log.warning("MS Word not available (%s); trying LibreOffice", exc)
    return _print_with_libreoffice(path, printer)


class _WordMissing(Exception):
    pass


def _print_with_word(path: Path, printer: str) -> str:
    import pythoncom
    import pywintypes
    import win32com.client

    pythoncom.CoInitialize()
    word = None
    try:
        try:
            word = win32com.client.DispatchEx("Word.Application")
        except pywintypes.com_error as exc:
            raise _WordMissing(str(exc))
        word.Visible = False
        word.DisplayAlerts = 0  # wdAlertsNone
        try:
            # FileName, ConfirmConversions, ReadOnly, AddToRecentFiles
            doc = word.Documents.Open(str(path), False, True, False)
        except pywintypes.com_error as exc:
            raise PrintFailed(f"Word could not open {path.name}: {exc}")
        try:
            try:
                # Selects the printer for this Word session without changing the Windows default.
                word.WordBasic.FilePrintSetup(Printer=printer, DoNotSetAsSysDefault=1)
            except pywintypes.com_error as exc:
                log.warning("FilePrintSetup failed (%s); setting ActivePrinter instead", exc)
                word.ActivePrinter = printer
            doc.PrintOut(Background=False)
            # give the spooler a moment before closing Word
            time.sleep(1.0)
        except pywintypes.com_error as exc:
            raise PrintFailed(f"Word failed to print {path.name}: {exc}")
        finally:
            doc.Close(SaveChanges=0)
        return "sent (MS Word)"
    finally:
        if word is not None:
            try:
                word.Quit(SaveChanges=0)
            except Exception:
                pass
        pythoncom.CoUninitialize()


def find_soffice() -> Path | None:
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        p = Path(base) / "LibreOffice" / "program" / "soffice.exe"
        if p.is_file():
            return p
    reg = _app_path_from_registry("soffice.exe")
    if reg:
        return reg
    found = shutil.which("soffice.exe")
    return Path(found) if found else None


def _print_with_libreoffice(path: Path, printer: str) -> str:
    soffice = find_soffice()
    if not soffice:
        raise PrintFailed(f"Neither MS Word nor LibreOffice is installed - cannot print {path.name}")
    cmd = [str(soffice), "--headless", "--norestore", "--pt", printer, str(path)]
    log.info("Printing with LibreOffice: %s", subprocess.list2cmdline(cmd))
    try:
        proc = subprocess.run(cmd, timeout=180, creationflags=CREATE_NO_WINDOW, capture_output=True)
    except subprocess.TimeoutExpired:
        raise PrintFailed(f"LibreOffice timed out printing {path.name}")
    if proc.returncode != 0:
        raise PrintFailed(f"LibreOffice exit code {proc.returncode}: {proc.stderr[:300]!r}")
    return "sent (LibreOffice)"


# ------------------------------------------------------------------ dispatch

def print_document(path: Path, printer: str, use_default: bool, sumatra_cfg: str = "", dry_run: bool = False) -> str:
    ext = path.suffix.lower()
    if dry_run:
        log.info("[dry-run] would print %s on %s", path, printer)
        return "dry-run"
    if ext == ".pdf":
        return print_pdf(path, printer, use_default, sumatra_cfg)
    if ext in (".doc", ".docx"):
        return print_word(path, printer)
    raise PrintFailed(f"Unsupported file type: {path.name}")


# ------------------------------------------------------------------ housekeeping

PRINT_LOG_FIELDS = ["timestamp", "file", "sender", "pages", "printer", "status"]


def append_print_log(file: str, sender: str, pages: int | None, printer: str, status: str) -> None:
    path = paths.print_log_path()
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        if new:
            writer.writerow(PRINT_LOG_FIELDS)
        writer.writerow([datetime.now().isoformat(timespec="seconds"), file, sender, "" if pages is None else pages, printer, status])


def cleanup_downloads(days: int) -> int:
    cutoff = time.time() - days * 86400
    removed = 0
    for f in paths.downloads_dir().iterdir():
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError as exc:
            log.warning("Could not delete old download %s: %s", f, exc)
    if removed:
        log.info("Deleted %d downloaded files older than %d days", removed, days)
    return removed
