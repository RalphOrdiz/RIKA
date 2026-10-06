"""'Print <filename>': find the attachment in Gmail, confirm out loud, print it."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from rika import paths
from rika.actions import printer as prn
from rika.errors import Cancelled, PrintFailed
from rika.intents import CANCEL, PRINT_RE, parse_choice, parse_yes_no
from rika.lines import ORDINALS, join_names, line
from rika.mailbox import Attachment, Mailbox
from rika.normalize import normalize_filename, split_ext, squash

if TYPE_CHECKING:
    from rika.assistant import Assistant

log = logging.getLogger(__name__)


@dataclass
class Match:
    att: Attachment
    score: float
    exact: bool


# ------------------------------------------------------------------ matching

def names_equal(wanted: str, actual: str) -> bool:
    w_stem, w_ext = split_ext(wanted)
    a_stem, a_ext = split_ext(actual)
    if not a_ext:
        return False
    return squash(w_stem) == squash(a_stem) and (not w_ext or w_ext == a_ext)


def fuzzy_score(wanted: str, actual: str) -> float:
    w_stem, w_ext = split_ext(wanted)
    a_stem, a_ext = split_ext(actual)
    sw, sa = squash(w_stem), squash(a_stem)
    if not sw or not sa:
        return 0.0
    score = max(
        fuzz.ratio(sw, sa),
        fuzz.token_sort_ratio(w_stem.replace("_", " ").replace("-", " "), a_stem.replace("_", " ").replace("-", " ")),
    )
    if len(sw) >= 4 and sw in sa:
        score = max(score, 85 + 15 * len(sw) / len(sa))  # spoken name is a chunk of the real one
    if w_ext and a_ext and w_ext != a_ext:
        score -= 10
    return min(score, 100.0)


def _dedupe(atts: list[Attachment]) -> list[Attachment]:
    seen, out = set(), []
    for a in atts:
        key = (a.message_id, a.filename.lower())
        if key not in seen:
            seen.add(key)
            out.append(a)
    return out


def find_matches(
    mailbox: Mailbox, name: str, days: int, max_messages: int, threshold: float
) -> tuple[list[Match], list[Attachment]]:
    """Return (matches, closest). Exact matches win; otherwise fuzzy matches above threshold."""
    exact = [a for a in mailbox.search_by_name(name) if names_equal(name, a.filename)]
    if exact:
        exact = _dedupe(sorted(exact, key=lambda a: a.received, reverse=True))
        return [Match(a, 100.0, True) for a in exact], []

    recent = _dedupe(mailbox.recent_attachments(days, max_messages))
    exact = [a for a in recent if names_equal(name, a.filename)]
    if exact:
        return [Match(a, 100.0, True) for a in exact], []

    scored = sorted(
        (Match(a, fuzzy_score(name, a.filename), False) for a in recent),
        key=lambda m: (m.score, m.att.received),
        reverse=True,
    )
    matches = [m for m in scored if m.score >= threshold]
    closest: list[Attachment] = []
    for m in scored:
        if m.att.filename.lower() not in {c.filename.lower() for c in closest}:
            closest.append(m.att)
        if len(closest) == 3:
            break
    log.info(
        "Fuzzy search for %r: %s",
        name,
        ", ".join(f"{m.att.filename}={m.score:.0f}" for m in scored[:5]) or "no recent attachments",
    )
    return matches, closest


# ------------------------------------------------------------------ speech helpers

def spoken_when(dt: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    clock = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == now.date():
        return f"today at {clock}"
    if dt.date() == (now - timedelta(days=1)).date():
        return f"yesterday at {clock}"
    if (now - dt).days < 7:
        return f"on {dt.strftime('%A')}"
    return f"on {dt.strftime('%B')} {dt.day}"


def short_sender(name: str) -> str:
    words = name.split()
    return " ".join(words[:2]) if words else "someone"


# ------------------------------------------------------------------ flow

def run_print(asst: "Assistant", filename: str) -> None:
    io, cfg = asst.io, asst.cfg

    if not filename:
        reply = io.ask(line("ask_filename"), hint="document1.pdf, report.docx")
        if reply:
            m = PRINT_RE.search(reply.lower())
            filename = normalize_filename(reply[m.end():] if m else reply)
        if not filename:
            io.say(line("not_understood"))
            return

    mailbox = asst.mailbox()
    if hasattr(mailbox, "connect"):
        mailbox.connect()  # fail fast on missing/expired login before "searching..."
    io.say(line("searching", name=filename))
    matches, closest = find_matches(
        mailbox, filename, cfg["search_days"], cfg["max_search_messages"], cfg["fuzzy_threshold"]
    )
    io.check_cancel()

    if not matches:
        io.say(line("not_found"))
        if closest:
            io.say(line("closest", names=join_names([c.filename for c in closest])))
        else:
            io.say(line("nothing_recent", days=cfg["search_days"]))
        return

    chosen = matches[0].att if len(matches) == 1 else _choose(asst, [m.att for m in matches[:3]])
    print_attachment(asst, chosen)


def run_list(asst: "Assistant") -> None:
    """'Any files to print?' - read the newest printable attachments and offer to print one."""
    io, cfg = asst.io, asst.cfg
    mailbox = asst.mailbox()
    if hasattr(mailbox, "connect"):
        mailbox.connect()
    io.say(line("checking_inbox"))
    atts = _dedupe(mailbox.recent_attachments(cfg["search_days"], cfg["max_search_messages"]))
    atts.sort(key=lambda a: a.received, reverse=True)
    io.check_cancel()
    if not atts:
        io.say(line("nothing_recent", days=cfg["search_days"]))
        return

    if len(atts) == 1:
        a = atts[0]
        io.say(line("file_found_one"))
        io.say(f"{a.filename}, from {short_sender(a.sender_name)}, {spoken_when(a.received)}.")
        print_attachment(asst, a)  # asks "Print ... Yes or no?" itself
        return

    top = atts[:3]
    io.say(line("files_found", count=len(atts), days=cfg["search_days"]))
    if len(atts) > 3:
        io.say(line("files_newest"))
    _read_options(asst, top)
    labels = [f"{a.filename} {a.sender_name}" for a in top]
    prompt = line("offer_print_choice" if len(top) == 3 else "offer_print_choice_two")
    for _ in range(3):
        reply = io.ask(prompt, hint="first, second, third, no")
        if reply is None:
            break
        choice = parse_choice(reply, len(top), labels)
        if choice == CANCEL or parse_yes_no(reply) is False:
            break
        if isinstance(choice, int):
            print_attachment(asst, top[choice])
            return
        prompt = line("choose_again")
    io.say(line("offer_declined"))


def print_attachment(asst: "Assistant", chosen: Attachment) -> None:
    """Download, ALWAYS confirm out loud (with page count and printer), print, log."""
    io, cfg = asst.io, asst.cfg
    mailbox = asst.mailbox()

    # check the printer before downloading so we fail fast
    printer_name, use_default = prn.resolve_printer(cfg.printer)
    if not asst.dry_run:
        prn.check_printer_ready(printer_name)

    io.say(line("downloading"))
    path = mailbox.download(chosen, paths.downloads_dir())
    pages = prn.count_pages(path)
    sender = short_sender(chosen.sender_name)

    question = line(
        "confirm_print",
        name=chosen.filename,
        sender=sender,
        pages=prn.pages_phrase(pages, chosen.ext),
        printer=prn.short_printer_name(printer_name),
    )
    if not asst.ask_yes_no(question):
        io.say(line("print_declined"))
        return

    io.say(line("printing"))
    try:
        status = prn.print_document(path, printer_name, use_default, cfg.get("sumatra_path", ""), asst.dry_run)
    except PrintFailed as exc:
        prn.append_print_log(chosen.filename, chosen.sender_name, pages, printer_name, f"failed: {exc}")
        raise
    prn.append_print_log(chosen.filename, chosen.sender_name, pages, printer_name, status)
    log.info("Print job: %s from %s, %s pages, %s -> %s", chosen.filename, chosen.sender_name, pages, printer_name, status)
    io.say(line("done"))


def _read_options(asst: "Assistant", options: list[Attachment]) -> None:
    for i, att in enumerate(options):
        asst.io.say(
            line(
                "choice_item",
                ordinal=ORDINALS[i],
                name=att.filename,
                sender=short_sender(att.sender_name),
                when=spoken_when(att.received),
            )
        )


def _choose(asst: "Assistant", options: list[Attachment]) -> Attachment:
    io = asst.io
    io.say(line("choices_intro", count=len(options)))
    for i, att in enumerate(options):
        io.say(
            line(
                "choice_item",
                ordinal=ORDINALS[i],
                name=att.filename,
                sender=short_sender(att.sender_name),
                when=spoken_when(att.received),
            )
        )
    labels = [f"{a.filename} {a.sender_name}" for a in options]
    prompt = line("choose_prompt" if len(options) == 3 else "choose_prompt_two")
    for attempt in range(3):
        reply = io.ask(prompt, hint="first, second, third, cancel")
        choice = parse_choice(reply or "", len(options), labels)
        if choice == CANCEL:
            raise Cancelled()
        if isinstance(choice, int):
            return options[choice]
        prompt = line("choose_again")
    raise Cancelled()
