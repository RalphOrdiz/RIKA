"""End-to-end conversation tests for 'print <file>' with a fake Gmail and printer."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from rika.actions import gmail_print, printer
from rika.assistant import Assistant
from rika.config import load_config
from rika.errors import NoInternet, PrinterOffline
from rika.interaction import ScriptedIO
from rika.lines import LINES
from rika.mailbox import Attachment

NOW = datetime.now()


def att(name, sender="Juan Dela Cruz", hours_ago=1, mid=None):
    return Attachment(
        message_id=mid or f"m-{name}-{sender}-{hours_ago}",
        attachment_id="a1",
        filename=name,
        sender_name=sender,
        sender_email="x@example.com",
        received=NOW - timedelta(hours=hours_ago),
    )


class FakeMailbox:
    def __init__(self, attachments, fail=None):
        self.attachments = attachments
        self.fail = fail
        self.downloaded = []

    def search_by_name(self, name):
        if self.fail:
            raise self.fail
        return [a for a in self.attachments if gmail_print.names_equal(name, a.filename)]

    def recent_attachments(self, days, max_messages):
        return list(self.attachments)

    def download(self, a, dest_dir):
        path = Path(dest_dir) / a.filename
        path.write_bytes(b"%PDF-fake")
        self.downloaded.append(a)
        return path


@pytest.fixture
def printed(monkeypatch):
    jobs = []
    monkeypatch.setattr(printer, "resolve_printer", lambda name: ("EPSON L120 Series", False))
    monkeypatch.setattr(printer, "check_printer_ready", lambda name: None)
    monkeypatch.setattr(printer, "count_pages", lambda path: 3)

    def fake_print(path, name, use_default, sumatra, dry_run):
        jobs.append((Path(path).name, name))
        return "sent (fake)"

    monkeypatch.setattr(printer, "print_document", fake_print)
    return jobs


def said(io, key):
    """True if Rika said any line from pool ``key`` (lines are picked at random)."""
    pool = LINES[key]
    return any(s in pool for s in io.said)


def make(attachments, replies, **kw):
    cfg = load_config()
    io = ScriptedIO(replies)
    box = FakeMailbox(attachments, **kw)
    return Assistant(cfg, io, mailbox_factory=lambda: box), io, box


def test_single_exact_match_confirm_yes(printed):
    asst, io, box = make([att("document1.pdf", "Juan")], ["yes"])
    asst.handle("Rika, print document one dot pdf")
    assert printed == [("document1.pdf", "EPSON L120 Series")]
    assert any("Print document1.pdf from Juan, 3 pages, on EPSON L120? Yes or no?" == s for s in io.said)
    assert said(io, "printing") and said(io, "done")
    assert io.said[-1] in LINES["done"]
    from rika import paths

    rows = paths.print_log_path().read_text(encoding="utf-8-sig").splitlines()
    assert rows[0] == "timestamp,file,sender,pages,printer,status"
    assert "document1.pdf,Juan,3,EPSON L120 Series,sent (fake)" in rows[1]


def test_confirm_no_does_not_print(printed):
    asst, io, _ = make([att("document1.pdf")], ["nope"])
    asst.handle("print document1.pdf")
    assert printed == []
    assert said(io, "print_declined")


def test_unclear_confirmation_three_times_means_no(printed):
    asst, io, _ = make([att("document1.pdf")], ["hmm", "what", "banana"])
    asst.handle("print document1.pdf")
    assert printed == []


def test_several_matches_choose_second(printed):
    files = [att("report.pdf", "Pedro", 1), att("report.pdf", "Juan", 5), att("report.pdf", "Maria", 30)]
    asst, io, box = make(files, ["the second one", "yes"])
    asst.handle("print report.pdf")
    assert box.downloaded[0].sender_name == "Juan"
    assert printed and printed[0][0] == "report.pdf"
    assert any(s.startswith("First: report.pdf, from Pedro") for s in io.said)
    assert any(s.startswith("Third: report.pdf, from Maria") for s in io.said)


def test_several_matches_cancel(printed):
    files = [att("report.pdf", "Pedro", 1), att("report.pdf", "Juan", 5)]
    asst, io, box = make(files, ["cancel"])
    asst.handle("print report.pdf")
    assert printed == [] and box.downloaded == []
    assert said(io, "cancelled")


def test_fuzzy_match_single(printed):
    asst, io, box = make([att("Document_1_final.pdf"), att("photo.pdf")], ["sure"])
    asst.handle("print document one final dot pdf")
    assert box.downloaded[0].filename == "Document_1_final.pdf"
    assert printed


def test_not_found_reads_three_closest(printed):
    files = [att("invoice.pdf"), att("receipt.pdf"), att("resume.docx"), att("letter.doc")]
    asst, io, _ = make(files, [])
    asst.handle("print banana split dot pdf")
    assert printed == []
    assert said(io, "not_found")
    closest = [s for s in io.said if s.startswith("The closest ones are")]
    assert closest and closest[0].count(".") >= 3


def test_nothing_recent(printed):
    asst, io, _ = make([], [])
    asst.handle("print document1.pdf")
    assert any("last 14 days" in s for s in io.said)


def test_ask_filename_when_missing(printed):
    asst, io, box = make([att("document1.pdf")], ["document one dot pdf", "yes"])
    asst.handle("Rika, print")
    assert io.said[0] in LINES["ask_filename"]
    assert printed


def test_no_internet_is_friendly(printed):
    asst, io, _ = make([], [], fail=NoInternet("dns failed"))
    asst.handle("print document1.pdf")
    assert said(io, "error_no_internet")


def test_printer_offline_is_friendly(printed, monkeypatch):
    def offline(name):
        raise PrinterOffline("offline")

    monkeypatch.setattr(printer, "check_printer_ready", offline)
    asst, io, _ = make([att("document1.pdf")], ["yes"])
    asst.handle("print document1.pdf")
    assert printed == []
    assert said(io, "error_printer_offline")


def test_stop_and_help_and_unknown():
    asst, io, _ = make([], [])
    asst.handle("Rika, what can you do?")
    asst.handle("Rika, stop")
    asst.handle("Rika, what's the weather")
    assert io.said[0] in LINES["help"]
    assert io.said[1] in LINES["stop_sleep"]
    assert io.said[-1] in LINES["out_of_scope"]
    asst.handle("asdf qwerty")
    assert io.said[-1] in LINES["not_understood"]


@pytest.mark.parametrize(
    "text, pool",
    [("hello", "greeting"), ("thanks", "thanks"), ("who are you", "about"), ("how are you", "how_are_you"),
     ("that's all", "sleep"), ("send an email to Juan", "out_of_scope")],
)
def test_small_talk(text, pool):
    asst, io, _ = make([], [])
    asst.handle(text)
    assert io.said == [io.said[0]] and io.said[0] in LINES[pool]


def test_only_about_lines_say_her_name():
    # her own voice saying "Rika" is muted for the wake word (Speaker.said_own_name_recently);
    # keep the name to the "about" answers so that guard stays the exception, not the rule
    for key, pool in LINES.items():
        for text in pool:
            assert ("rika" in text.lower()) == (key == "about" and "Rika" in text), (key, text)
    asst, io, _ = make([], [])
    asst.handle("what's your name?")
    assert "Rika" in io.said[-1]


# ------------------------------------------------------------ "any files to print?"

def test_list_files_choose_second(printed):
    files = [att("invoice.pdf", "Pedro", 1), att("report.docx", "Juan", 5), att("id.pdf", "Maria", 30),
             att("old.pdf", "Ana", 70)]
    asst, io, box = make(files, ["the second one", "yes"])
    asst.handle("Rika, do you see a printable file in my gmail?")
    assert said(io, "checking_inbox") and said(io, "files_newest")
    assert "I see 4 printable files from the last 14 days!" in io.said
    assert any(s.startswith("First: invoice.pdf, from Pedro") for s in io.said)
    assert not any("old.pdf" in s for s in io.said)  # only the newest 3 are read out
    assert box.downloaded[0].filename == "report.docx"
    assert printed == [("report.docx", "EPSON L120 Series")]


def test_list_files_single_file_still_confirms(printed):
    asst, io, _ = make([att("flyer.pdf", "Pedro")], ["yes"])
    asst.handle("any files to print?")
    assert said(io, "file_found_one")
    assert any(s.startswith("Print flyer.pdf from Pedro, 3 pages") for s in io.said)
    assert printed == [("flyer.pdf", "EPSON L120 Series")]


def test_list_files_say_no(printed):
    asst, io, box = make([att("a.pdf"), att("b.pdf", "Ana")], ["no thanks"])
    asst.handle("is there anything to print")
    assert printed == [] and box.downloaded == []
    assert io.said[-1] in LINES["offer_declined"]


def test_list_files_nothing(printed):
    asst, io, _ = make([], [])
    asst.handle("any new documents?")
    assert io.said[-1] == "Hmm, I don't see any PDF or Word files from the last 14 days."


def test_printer_status(printed):
    asst, io, _ = make([], [])
    asst.handle("is the printer ready?")
    assert io.said[-1] in [t.format(printer="EPSON L120") for t in LINES["printer_ready"]]


def test_printer_status_offline(printed, monkeypatch):
    def offline(name):
        raise PrinterOffline("offline")

    monkeypatch.setattr(printer, "check_printer_ready", offline)
    asst, io, _ = make([], [])
    asst.handle("printer status")
    assert said(io, "error_printer_offline")


def test_open_gmail_dry_run(monkeypatch):
    from rika.actions import gmail_open

    launched = []
    monkeypatch.setattr(gmail_open, "find_chrome", lambda cfg="": Path("chrome.exe"))
    monkeypatch.setattr(gmail_open, "list_chrome_profiles", lambda: [])
    monkeypatch.setattr(gmail_open, "chrome_windows", lambda: [])  # never touch real windows in tests
    monkeypatch.setattr(
        gmail_open.subprocess, "Popen", lambda cmd, **kw: launched.append(cmd)
    )
    asst, io, _ = make([], [])
    asst.handle("Rika, open rjoprintingservice gmail")
    assert launched == [[
        "chrome.exe",
        "--profile-directory=Profile 2",
        "--start-maximized",
        "https://mail.google.com/mail/u/?authuser=rjoprintingservice%40gmail.com",
    ]]
    assert any("RJO Printing Service" in s for s in io.said)


def test_open_unknown_account():
    asst, io, _ = make([], [])
    asst.handle("open facebook gmail")
    assert any("don't know that account" in s for s in io.said)  # error_account_not_found


def test_spoken_when():
    now = datetime(2026, 10, 6, 15, 0)
    assert gmail_print.spoken_when(datetime(2026, 10, 6, 9, 5), now) == "today at 9:05 AM"
    assert gmail_print.spoken_when(datetime(2026, 10, 5, 14, 30), now) == "yesterday at 2:30 PM"
    assert gmail_print.spoken_when(datetime(2026, 10, 2, 14, 30), now) == "on Friday"
    assert gmail_print.spoken_when(datetime(2026, 9, 3, 14, 30), now) == "on September 3"


def test_short_printer_name():
    assert printer.short_printer_name("EPSON L120 Series") == "EPSON L120"
    assert printer.short_printer_name("HP LaserJet (Copy 1)") == "HP LaserJet"
