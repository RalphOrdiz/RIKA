"""'Close RJO Gmail': only that account's Chrome windows get closed."""
import pytest

from rika.actions import gmail_open
from rika.assistant import Assistant
from rika.config import load_config
from rika.intents import parse
from rika.interaction import ScriptedIO
from rika.lines import LINES

TERMS = ["rjoprintingservice", "rjo", "rjo printing service", "shop"]
RJO = "rjoprintingservice@gmail.com"


@pytest.mark.parametrize(
    "text, account",
    [
        ("Rika, close the RJO Gmail", "rjo"),
        ("close rjo printing service gmail", "rjo printing service"),
        ("close the RJO printing service chrome", "rjo printing service"),
        ("close rjo", "rjo"),
        ("close chrome", None), ("close the browser", None), ("close it", None), ("Close the Gmail please", None),
        ("shut down chrome", None), ("quit gmail", None), ("can you close the gmail window", None),
        ("exit the browser", None), ("close all the tabs", None), ("close", None),
    ],
)
def test_close_intent(text, account):
    intent = parse(text, account_terms=TERMS)
    assert intent.name == "close_gmail", text
    assert intent.slots["account"] == account


def test_close_door_is_not_gmail():
    assert parse("close the door", account_terms=TERMS).name != "close_gmail"


@pytest.fixture
def windows(monkeypatch):
    """Fake desktop: Rika may only close the RJO windows."""
    state = {
        "open": {
            1: "Messenger | Facebook - Google Chrome",                      # owner's own profile
            2: f"Inbox (3) - {RJO} - Gmail - Google Chrome",              # RJO Gmail
            3: "Inbox - darkmagus001@gmail.com - Gmail - Google Chrome",  # someone else's Gmail
            4: "report.pdf - Google Chrome",                              # RJO window Rika opened earlier
        },
        "closed": [],
    }
    monkeypatch.setattr(gmail_open, "chrome_windows", lambda: list(state["open"].items()))

    import win32gui

    def fake_post(hwnd, msg, w, l):
        assert msg == gmail_open.WM_CLOSE
        state["closed"].append(hwnd)
        state["open"].pop(hwnd, None)

    monkeypatch.setattr(win32gui, "PostMessage", fake_post)
    gmail_open._opened_windows.clear()
    gmail_open._opened_windows[RJO] = {4, 99}  # 99 was already closed by hand
    yield state
    gmail_open._opened_windows.clear()


def test_closes_only_rjo_windows(windows):
    asst = Assistant(load_config(), ScriptedIO())
    asst.handle("Rika, close the RJO Gmail")
    assert sorted(windows["closed"]) == [2, 4]  # never 1 (owner's) or 3 (another account)
    assert asst.io.said[-1] in [t.format(account="RJO Printing Service") for t in LINES["closing_gmail"]]


def test_close_it_when_nothing_open(windows):
    windows["open"] = {1: "Messenger | Facebook - Google Chrome"}
    gmail_open._opened_windows.clear()
    asst = Assistant(load_config(), ScriptedIO())
    asst.handle("close chrome")
    assert windows["closed"] == []
    assert asst.io.said[-1] in [t.format(account="RJO Printing Service") for t in LINES["gmail_not_open"]]


def test_dry_run_closes_nothing(windows):
    asst = Assistant(load_config(), ScriptedIO(), dry_run=True)
    asst.handle("close rjo gmail")
    assert windows["closed"] == []
    assert asst.io.said[-1] in [t.format(account="RJO Printing Service") for t in LINES["closing_gmail"]]


def test_new_gmail_window_is_maximized_once(monkeypatch):
    calls = []
    monkeypatch.setattr(gmail_open, "chrome_windows", lambda: [
        (1, "Messenger | Facebook - Google Chrome"),
        (7, f"Inbox - {RJO} - Gmail - Google Chrome"),
    ])
    monkeypatch.setattr(gmail_open, "maximize_window", calls.append)
    gmail_open._opened_windows.clear()
    gmail_open._remember_new_windows(RJO, before={1}, wait_s=1.2, maximize=True)
    assert calls == [7]  # only the RJO window, and only once (you can still restore it yourself)
    assert gmail_open._opened_windows[RJO] == {7}
    gmail_open._opened_windows.clear()
