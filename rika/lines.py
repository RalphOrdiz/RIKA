"""Rika's lines: short, cute, playful and upbeat, with a small random pool per situation.

Lines are pre-rendered into the TTS cache at startup so Rika answers instantly.
Only lines that contain a file name or a sender's name are generated live
(see ``prerender_lines``). Only the "about" lines contain her name; the wake word
is muted while she says it (app.Controller._on_wake), so her own voice can't wake her.
"""
from __future__ import annotations

import random
import string
import threading

LINES: dict[str, list[str]] = {
    # waking up / listening
    "wake": ["Hi hi! I'm listening!", "Yes? What do you need?", "I'm all ears!", "Hiya! Go ahead!"],
    "no_speech": ["Hmm? I didn't hear anything!", "Oops, I didn't catch anything!", "Hello? Was that a whisper?"],
    "not_understood": [
        "Sorry, can you say that again?",
        "Hmm, I didn't get that. One more time?",
        "Oopsie! Can you say that again?",
    ],
    "help": [
        "I can open your Gmail, like: open RJO Gmail! I can check for files to print, like: anything to print? "
        "And I can print them, like: print document one dot PDF! Say stop anytime!",
    ],
    # conversation
    "greeting": ["Hi hi! What can I do for you?", "Hello! Ready to print something?", "Hiya! How can I help?"],
    "thanks": ["You're welcome!", "Anytime!", "Happy to help!"],
    "about": [
        "I'm Rika, the RJO Printing Service assistant! I open your Gmail and print files from it.",
        "I'm Rika, your printing buddy! I find files in your Gmail and print them for you.",
        "My name is Rika! I help RJO Printing Service with Gmail and printing.",
    ],
    "how_are_you": ["I'm super, thanks! Ready to print!", "Doing great! Got anything to print?"],
    "out_of_scope": [
        "Hmm, I can only open your Gmail and print files from it!",
        "Sorry, that's outside my job! I'm all about Gmail and printing.",
        "Ooh, I can't do that one! But I can check your Gmail for files to print.",
    ],
    # going back to sleep (she needs her name again after this)
    "sleep": [
        "Just call my name, I'll be sleeping for a while!",
        "Okay, nap time! Just call my name when you need me.",
        "I'll rest for now! Call my name anytime!",
    ],
    "stop_sleep": ["Okay, stopping! Just call my name if you need me.", "Okie, I'll be quiet! Call my name anytime."],
    # checking Gmail for printable files
    "checking_inbox": ["Let me peek at your Gmail!", "Checking your inbox!"],
    "files_found": ["I see {count} printable files from the last {days} days!"],
    "file_found_one": ["I see one printable file!"],
    "files_newest": ["Here are the newest ones."],
    "offer_print_choice": ["Want me to print one? Say first, second, or third. Or say no."],
    "offer_print_choice_two": ["Want me to print one? Say first or second. Or say no."],
    "offer_declined": ["Okay! Just ask if you need anything."],
    "printer_ready": ["The {printer} looks ready to go!", "Yep! The {printer} is ready!"],
    "cancelled": ["Okay, cancelled!", "No problem, stopping!", "Okie dokie, never mind!"],
    "stopped_nothing": ["I'm not doing anything right now!", "All quiet over here!"],
    "ready": ["Hi hi! I'm ready to help!", "Yay, I'm online! Just call my name!"],
    "paused": ["Okay, I'll take a little break!", "Break time! I'll stop listening for now."],
    "resumed": ["I'm back! Call my name anytime!", "Yay, I'm listening again!"],
    "done": ["All done!", "Yay, finished!", "Ta-da! All done!"],
    "new_voice": ["Hi hi! Do you like my new voice?", "Ta-da! How do I sound now?"],
    # Gmail
    "opening_gmail": ["Opening {account} Gmail now!", "Okay, {account} Gmail coming right up!", "Here comes {account} Gmail!"],
    "which_account": ["Which Gmail should I open? I know {accounts}."],
    "closing_gmail": ["Closing {account} Gmail! Bye bye, inbox!", "Okay, {account} Gmail is closed!",
                      "Done! {account} Gmail is closed."],
    "gmail_not_open": ["Hmm, I don't see {account} Gmail open right now.",
                       "I can't find an open {account} Gmail window!"],
    # printing
    "ask_filename": ["Which file should I print?", "Ooh, what's the file name?"],
    "searching": ["Okay, looking for {name}!", "Searching Gmail for {name}!", "Let me find {name}!"],
    "not_found": ["Hmm, I can't find that file...", "Uh oh, nothing matches that name."],
    "closest": ["The closest ones are {names}."],
    "nothing_recent": ["Hmm, I don't see any PDF or Word files from the last {days} days."],
    "choices_intro": ["I found {count} files!", "Ooh, {count} matches!"],
    "choice_item": ["{ordinal}: {name}, from {sender}, {when}."],
    "choose_prompt": ["Which one? First, second, or third?", "Which one should I print? First, second, or third?"],
    "choose_prompt_two": ["Which one? First or second?"],
    "choose_again": ["Sorry! Say first, second, or third. Or say cancel."],
    "downloading": ["Grabbing the file!", "Downloading it now!"],
    "confirm_print": ["Print {name} from {sender}{pages}, on {printer}? Yes or no?"],
    "confirm_again": ["Sorry! Just yes or no, please!", "Yes or no?"],
    "printing": ["Printing now~!", "On its way to the printer!", "Here it comes! Printing now!"],
    "print_declined": ["Okay, I won't print it!", "No problem, not printing!"],
    "reconnect_offer": ["Should I open the Google sign-in page now? Yes or no?"],
    "reconnect_wait": ["Okay! Please sign in on the page I just opened."],
    "reconnected": ["Yay, Gmail is connected! Please say your command again!"],
    # errors (short and friendly; the details go to rika.log)
    "error_generic": ["Uh oh, something went wrong! Please check my log.", "Oops! Something broke. Please check my log."],
    "error_no_mic": ["I can't hear anything! Is the microphone plugged in?"],
    "error_no_internet": ["Uh oh, I can't reach the internet! Please check the connection."],
    "error_gmail_not_configured": ["Gmail isn't connected yet! Please follow the setup guide."],
    "error_token_expired": ["Oops, my Gmail login expired! Please reconnect Gmail from my menu."],
    "error_gmail": ["Gmail gave me an error! Let's try again in a bit."],
    "error_account_not_found": ["Hmm, I don't know that account! I know {accounts}."],
    "error_chrome_not_found": ["Uh oh, I can't find Google Chrome on this PC!"],
    "error_printer_not_found": ["Hmm, I can't find the printer {printer}! Please check the name in my settings."],
    "error_printer_offline": ["Uh oh, the printer looks offline! Is it turned on?"],
    "error_print_failed": ["Oh no, printing failed! Please check the printer."],
    "error_config": ["Oops, my settings file has a problem! Please check it."],
    "config_problems": ["Some of my settings looked wrong, so I used the defaults! Please check my log."],
}

ORDINALS = ["First", "Second", "Third"]

# Placeholders that carry a file or sender name -> always generated live.
LIVE_FIELDS = {"name", "names", "sender", "when", "pages"}

_last: dict[str, str] = {}
_lock = threading.Lock()


def line(key: str, **fmt: object) -> str:
    """Pick a random line for ``key`` (avoiding an immediate repeat) and format it."""
    pool = LINES.get(key) or LINES["error_generic"]
    with _lock:
        choices = [p for p in pool if p != _last.get(key)] or pool
        template = random.choice(choices)
        _last[key] = template
    try:
        return template.format(**fmt)
    except (KeyError, IndexError):
        return template


def _fields(text: str) -> set[str]:
    return {f[1] for f in string.Formatter().parse(text) if f[1]}


def fixed_lines() -> list[str]:
    """All lines that have no placeholders."""
    return [t for pool in LINES.values() for t in pool if not _fields(t)]


def prerender_lines(values: dict[str, list[str]]) -> list[str]:
    """Every line Rika can say that does not contain a file or sender name.

    ``values`` maps placeholder -> possible values from config, e.g.
    {"account": ["RJO Printing Service"], "accounts": ["RJO Printing Service"],
     "printer": ["EPSON L120"], "days": ["14"], "count": ["2", "3"]}.
    """
    out: list[str] = []
    for pool in LINES.values():
        for template in pool:
            fields = _fields(template)
            if not fields:
                out.append(template)
            elif not fields & LIVE_FIELDS and all(values.get(f) for f in fields):
                combos: list[dict[str, str]] = [{}]
                for f in fields:
                    combos = [{**c, f: v} for c in combos for v in values[f]]
                out += [template.format(**c) for c in combos]
    return list(dict.fromkeys(out))


def join_names(names: list[str]) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
