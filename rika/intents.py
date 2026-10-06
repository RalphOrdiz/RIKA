"""Rule-based intent parser with fuzzy matching (rapidfuzz). No cloud LLM.

Intents:
    open_gmail      {account: str | None}
    print_file      {filename: str, spoken: str}   (also "do you see report.pdf?")
    list_files      {}      "any files to print?", "do you see a printable file in my gmail?"
    printer_status  {}      "is the printer ready?", "which printer do you use?"
    stop            {}
    sleep           {}      "that's all", "go to sleep", "bye"
    help            {}      "what can you do?", "what are you capable of?"
    greeting / thanks / about / how_are_you   small talk
    out_of_scope    {}      a question or request she can't do ("what's the weather?")
    wake_only       {}      just "Rika" with nothing after it
    unknown         {}

Also: parse_yes_no(), parse_choice(), match_account(). English only.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz, process

from rika.normalize import normalize_filename, squash

log = logging.getLogger(__name__)

DEFAULT_WAKE_WORDS = ["rika", "rica", "reeka", "rikka", "ricka", "reka", "rieka"]
INTERJECTIONS = {"hey", "hi", "hello", "okay", "ok", "okie", "uh", "um", "uhm", "so", "yo", "ah", "eh", "oh", "well",
                 "alright", "now", "and", "also", "then", "hmm", "erm"}
POLITE_PREFIXES = [
    "all right", "and then", "and now", "by the way", "one more thing",
    "can you please", "could you please", "would you please", "will you please", "can you", "could you",
    "would you", "will you", "would you mind", "i want you to", "i need you to", "i want to", "i need to",
    "i'd like to", "id like to", "let's", "lets", "please", "pls", "kindly", "help me", "go and", "just",
]

STOP_PHRASES = {
    "stop", "stop it", "stop that", "stop now", "stop please", "please stop", "cancel", "cancel it", "cancel that",
    "cancel please", "never mind", "nevermind", "forget it", "quiet", "be quiet", "shut up", "hush", "abort",
    "enough", "halt", "stop rika", "cancel rika", "okay stop", "ok stop", "stop talking", "nothing",
}
HELP_RE = re.compile(
    r"\b(?:what can you do|what do you do|what else can you do|what can i (?:say|ask)|how do i use you|"
    r"help|commands?|what are your commands|list (?:the )?commands|what are you able to do|capable|"
    r"capabilities|what can you help(?: me)?(?: with)?|how does this work|how do you work|what should i say|"
    r"how do i print|what do you know)\b"
)
SLEEP_RE = re.compile(
    r"^(?:(?:no |okay |ok |thanks |thank you )?(?:that'?s all|that is all|that'?s it|that is it|that'?ll be all|"
    r"i'?m done|we'?re done|all done|nothing else|no more|go to sleep|go back to sleep|sleep|take a nap|nap time|"
    r"you can (?:go|rest|sleep)(?: now)?|bye|goodbye|good bye|bye bye|see you|see you later|good night|"
    r"rest now|dismissed|power down|power off|standby|stand by|turn off|switch off|shut down|relax|chill|"
    r"time to (?:sleep|rest)|sleep time|bed ?time|that'?s enough(?: for (?:today|now))?|call it a day|"
    r"let'?s call it a day|off you go|i'?m all set|we'?re all set|rest your voice|you'?re off duty|"
    r"i don'?t need you(?: anymore| right now| for now)?))(?: (?:for now|for today|thanks|thank you|now|please))*$"
)
GREETINGS = {"hello", "hi", "hiya", "howdy", "good morning", "good afternoon", "good evening", "hi there",
             "hello there", "hey there", "morning"}
THANKS_RE = re.compile(r"^(?:thanks|thank you|thank you so much|thanks a lot|thanks so much|ty|great thanks|"
                       r"(?:okay |ok |great |perfect |nice |awesome |cool )?thank(?:s| you)(?: so much| a lot)?)$")
ABOUT_RE = re.compile(r"\b(?:who are you|what(?:'s| is) your name|what are you$|introduce yourself|"
                      r"tell me about yourself|who made you)\b")
HOW_ARE_YOU_RE = re.compile(r"\b(?:how are you(?: doing)?|how'?s it going|how do you do|are you (?:okay|ok|alright|there|awake))\b")
PRINTER_STATUS_RE = re.compile(
    r"\b(?:printer (?:status|state)|(?:is|are) (?:the |my |our )?printers? (?:on|ready|online|working|ok|okay|"
    r"connected|offline|off|available)|which printer|what printer|printer (?:ready|on|online|working|connected))\b"
)
# "any files to print?", "do you see a printable file in my gmail?", "what can I print?"
LIST_RE = re.compile(
    r"\b(?:any(?:thing)?|something|what|which|show|list|see|seeing|check|look|new|got|have|are there|is there|"
    r"did (?:i|we) get|did (?:anyone|someone|somebody) send|waiting)\b.*"
    r"\b(?:printables?|files?|documents?|attachments?|pdfs?|docs|to print|for printing|print jobs?|to be printed)\b"
    r"|\bwhat (?:can|should|do|could) (?:i|we) print\b"
)
# "do you see report.pdf?", "find resume dot pdf in my gmail"
FIND_RE = re.compile(
    r"^(?:do you see|can you see|did you see|do you have|is there|did (?:i|we) get|find|look for|search for|"
    r"search|check for|did (?:anyone|someone|somebody) send(?: me| us)?)\s+"
    r"(?:a |an |the |any |my )?(?:file |attachment |document )?(?:called |named )?(?P<rest>.+)$"
)
CLOSE_RE = re.compile(
    r"^(?:close out of|close down|close|shut down|shut off|shut|exit out of|exit|quit|get rid of)\b\s*(?P<target>.*)$"
)
# "we're closing, shut the gmail" - a closing verb later in the sentence, aimed at Gmail/Chrome
_CLOSE_ANYWHERE = re.compile(
    r"(?<![a-z])(?:close|shut|exit|quit)(?: down| out of)?\s+(?P<target>(?:the |my |our |that |this )?"
    r"(?:[a-z']+ ){0,3}(?:g ?mail|email|e-mail|inbox|chrome|browser|window|tab)s?(?![a-z]).*)$"
)
CLOSE_TARGET_RE = re.compile(
    r"(?<![a-z])(?:chrome|google chrome|the browser|browser|window|windows|tab|tabs|it|that|everything|all)(?![a-z])"
)
OUT_OF_SCOPE_VERBS = re.compile(
    r"^(?:send|reply|forward|delete|compose|write|read|call|text|play|google|remind|set|turn|translate|"
    r"calculate|order|buy|book|tell me a joke|sing|weather)\b"
)
QUESTION_STARTS = re.compile(
    r"^(?:what|what's|whats|who|who's|when|where|why|how|which|is|are|was|were|do|does|did|can|could|will|would|"
    r"should|tell me|explain|give me|show me)\b"
)

PRINT_RE = re.compile(r"(?<![a-z])print(?![a-z])(?:\s+out\b)?")
PRINT_FUZZY_WORDS = ["print", "prints", "prent", "prince", "printt"]
OPEN_RE = re.compile(
    r"(?<![a-z])(?:open|opens|opened|opn|launch|go to|goto|"
    r"show me|show|check|access|log in to|login to|sign in to|pull up|bring up)(?![a-z])"
)
OPEN_FUZZY_WORDS = ["open"]
GMAIL_RE = re.compile(r"(?<![a-z])(?:g[- ]?mail|gee mail|gmale|g male|e[- ]?mail|email|mail|inbox|jimail|jemail)(?![a-z])")
ACCOUNT_FILLERS = {
    "the", "my", "our", "of", "for", "account", "accounts", "please", "inbox", "page", "tab", "in", "chrome",
    "on", "from", "to", "me", "up", "now",
}


@dataclass
class Intent:
    name: str
    slots: dict = field(default_factory=dict)
    text: str = ""


def _basic_clean(text: str) -> str:
    text = text.replace("﻿", "").lower().strip()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[,!?;:\"()\[\]{}~*]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[.\s]+$", "", text)  # sentence-final period
    text = re.sub(r"^[.\s]+", "", text)
    return text


def clean_utterance(text: str, wake_words: Iterable[str] = DEFAULT_WAKE_WORDS) -> str:
    """Lowercase, drop punctuation, leading wake words, interjections and politeness."""
    text = _basic_clean(text)
    wake = [w.lower() for w in wake_words]
    changed = True
    while changed and text:
        changed = False
        first, _, rest = text.partition(" ")
        first_bare = first.strip(".")
        if first_bare in INTERJECTIONS or first_bare in wake:
            text, changed = rest.strip(), True
            continue
        if wake and len(first_bare) >= 3 and process.extractOne(first_bare, wake, scorer=fuzz.ratio, score_cutoff=80):
            text, changed = rest.strip(), True
            continue
        for p in POLITE_PREFIXES:
            if text == p or text.startswith(p + " "):
                text, changed = text[len(p):].strip(), True
                break
    return text


def _fuzzy_first_word(text: str, choices: list[str], cutoff: int = 80) -> bool:
    first = text.split(" ", 1)[0] if text else ""
    return bool(first) and process.extractOne(first, choices, scorer=fuzz.ratio, score_cutoff=cutoff) is not None


def _account_phrase(text: str) -> str:
    text = re.sub(r"(?:@| at )g[- ]?mail(?:\.| dot )com", " gmail ", text)
    text = OPEN_RE.sub(" ", text)
    text = GMAIL_RE.sub(" ", text)
    words = [w for w in text.split() if w not in ACCOUNT_FILLERS]
    return " ".join(words).strip(" .")


def _without_wake_words(text: str, wake_words: Iterable[str]) -> str:
    words = [w for w in text.split() if w.strip(".") not in set(wake_words)]
    return " ".join(words)


_EXT_END = re.compile(r"(?:\.|\s+dot\s+|\s+)(?:pdf|p d f|docx|doc x|docs|dox|docks|doc|dock)(?![a-z])")


def _cut_after_extension(text: str) -> str:
    """'report.pdf from a customer' -> 'report.pdf' (drop words after the file name)."""
    m = _EXT_END.search(text)
    return text[: m.end()] if m else text


def _named_file(rest: str) -> str:
    """A file name with a .pdf/.doc/.docx extension in ``rest``, or ''."""
    from rika.normalize import split_ext

    rest = re.sub(r"\b(?:in|on|from) (?:my |the |our )?(?:gmail|g mail|email|e-mail|inbox|mail)\b.*$", "", rest).strip()
    name = normalize_filename(_cut_after_extension(rest))
    return name if split_ext(name)[1] else ""


def find_account_term(text: str, account_terms: Iterable[str]) -> str | None:
    """An account name/alias mentioned anywhere in ``text`` (fuzzy), e.g. 'rjo printing service'."""
    squashed = squash(text)
    best, best_score = None, 0.0
    for term in account_terms:
        s = squash(term)
        if len(s) < 3:
            continue
        score = fuzz.partial_ratio(s, squashed)
        if score >= 90 and (score, len(s)) > (best_score, len(squash(best or ""))):
            best, best_score = term, score
    return best


# Intents the meaning matcher may produce; slots are filled from the text where needed.
def _semantic_intent(name: str, text: str, raw: str, account_terms: Iterable[str]) -> Intent:
    if name in ("open_gmail", "close_gmail"):
        return Intent(name, {"account": find_account_term(text, account_terms)}, raw)
    if name == "print_file":
        filename = _named_file(text)
        return Intent(name, {"filename": filename, "spoken": text}, raw)
    return Intent(name, {}, raw)


def parse(
    text: str,
    wake_words: Iterable[str] = DEFAULT_WAKE_WORDS,
    account_terms: Iterable[str] = (),
    semantic=None,
) -> Intent:
    """Rules first (they extract file and account names precisely); if they don't recognise the
    request, the meaning matcher (``semantic``, optional) decides what was meant."""
    account_terms = list(account_terms)
    intent = _parse_rules(text, wake_words, account_terms)
    if semantic is None or not intent.slots.get("_fallback"):
        intent.slots.pop("_fallback", None)
        return intent
    # her name anywhere ("time to sleep, Rika") carries no meaning - drop it before comparing
    cleaned = _without_wake_words(clean_utterance(text, wake_words), [w.lower() for w in wake_words])
    name, score = semantic.classify(cleaned)
    if name:
        log.info("Meaning match: %r -> %s (%.2f)", cleaned, name, score)
        return _semantic_intent(name, cleaned, text, account_terms)
    intent.slots.pop("_fallback", None)
    return intent


def _parse_rules(text: str, wake_words: Iterable[str], account_terms: list[str]) -> Intent:
    raw = text
    original = _basic_clean(text)
    wake = [w.lower() for w in wake_words]
    bare = _without_wake_words(original, wake)  # "good morning rika" -> "good morning"
    text = clean_utterance(text, wake_words)

    if bare in GREETINGS:
        return Intent("greeting", {}, raw)
    if not text:
        return Intent("wake_only" if original else "unknown", {}, raw)

    if text in STOP_PHRASES or (len(text.split()) <= 3 and re.match(r"^(?:stop|cancel|never ?mind)\b", text)):
        return Intent("stop", {}, raw)
    if SLEEP_RE.match(_without_wake_words(text, wake)):
        return Intent("sleep", {}, raw)
    if THANKS_RE.match(_without_wake_words(text, wake)):
        return Intent("thanks", {}, raw)

    print_m = PRINT_RE.search(text)
    open_m = OPEN_RE.search(text)

    # "close rjo gmail", "close chrome", "close it" (after opening), "shut down the browser"
    close_m = CLOSE_RE.match(text) or _CLOSE_ANYWHERE.search(text)
    if close_m:
        target = close_m.group("target") or ""
        if not target.strip() or CLOSE_TARGET_RE.search(target) or GMAIL_RE.search(target):
            target = re.sub(r"(?<![a-z])(?:close|shut|exit|quit|down|out|of)(?![a-z])", " ", target)  # "close. close it"
            phrase = _account_phrase(CLOSE_TARGET_RE.sub(" ", target))
            return Intent("close_gmail", {"account": phrase or None}, raw)
        if account_terms:
            terms = [squash(t) for t in account_terms if t]
            if process.extractOne(squash(_account_phrase(target)), terms, scorer=fuzz.ratio, score_cutoff=85):
                return Intent("close_gmail", {"account": _account_phrase(target)}, raw)

    # "do you see report.pdf?" / "find resume dot pdf" -> go straight to that file
    find_m = FIND_RE.match(text)
    if find_m and _named_file(find_m.group("rest")):
        name = _named_file(find_m.group("rest"))
        return Intent("print_file", {"filename": name, "spoken": find_m.group("rest")}, raw)

    # "any files to print?", "do you see a printable file in my gmail?" - unless a real file name follows "print"
    if LIST_RE.search(text) and not (print_m and _named_file(text[print_m.end():])):
        return Intent("list_files", {}, raw)

    if re.match(r"^how (?:do|can|should|would) (?:i|we|you) (?:print|use|open)\b", text):
        return Intent("help", {}, raw)  # "how do I print?" is a question, not a print command
    if PRINTER_STATUS_RE.search(text):
        return Intent("printer_status", {}, raw)
    if OUT_OF_SCOPE_VERBS.match(text):
        return Intent("out_of_scope", {}, raw)

    # Fuzzy fallback for a mis-heard first word ("prints", "prince", "opn")
    if not print_m and not open_m:
        first, _, rest = text.partition(" ")
        if first not in ("printer", "printers", "printing", "printed") and _fuzzy_first_word(text, PRINT_FUZZY_WORDS, 85):
            return _print_intent(rest, "", raw)
        if _fuzzy_first_word(text, OPEN_FUZZY_WORDS, 80) and GMAIL_RE.search(text):
            return Intent("open_gmail", {"account": _account_phrase(rest) or None}, raw)

    if print_m and (not open_m or print_m.start() < open_m.start()):
        after = text[print_m.end():]
        before = text[: print_m.start()]
        return _print_intent(after, before, raw)

    if open_m or GMAIL_RE.search(text):
        has_gmail = bool(GMAIL_RE.search(text))
        phrase = _account_phrase(text)
        if has_gmail:
            if open_m or len(text.split()) <= 5:
                return Intent("open_gmail", {"account": phrase or None}, raw)
        elif open_m and phrase and account_terms:
            terms = [squash(t) for t in account_terms if t]
            if process.extractOne(squash(phrase), terms, scorer=fuzz.ratio, score_cutoff=85):
                return Intent("open_gmail", {"account": phrase}, raw)

    if HELP_RE.search(text):
        return Intent("help", {}, raw)
    if ABOUT_RE.search(text):
        return Intent("about", {}, raw)
    if HOW_ARE_YOU_RE.search(text):
        return Intent("how_are_you", {}, raw)

    # A question or request we don't handle -> say what she *can* do instead of "say that again".
    # "_fallback" lets the meaning matcher take a second look first.
    if QUESTION_STARTS.match(text) or raw.strip().endswith("?"):
        return Intent("out_of_scope", {"_fallback": True}, raw)
    return Intent("unknown", {"_fallback": True}, raw)


def _print_intent(after: str, before: str, raw: str) -> Intent:
    after = re.sub(r"^(?:out|please|for me)\b\s*", "", after.strip())
    spoken = after.strip()
    if re.fullmatch(r"(?:it|this|that)", spoken):
        spoken = ""
    if not normalize_filename(spoken) and before.strip():
        spoken = before.strip()  # "document1.pdf, print it"
    filename = normalize_filename(_cut_after_extension(spoken))
    return Intent("print_file", {"filename": filename, "spoken": spoken}, raw)


# ---------------------------------------------------------------- yes / no

# Core words from the spec first, then a few everyday English variants Whisper produces.
YES_PHRASES = [
    "yes", "yeah", "yep", "sure", "go", "print it",
    "yea", "yup", "okay", "ok", "go ahead", "do it", "yes please", "of course", "absolutely", "please do",
]
NO_PHRASES = [
    "no", "nope", "cancel", "stop",
    "nah", "don't", "dont", "do not", "no thanks", "never mind", "nevermind", "not yet", "wait", "abort",
]


def _phrase_hit(text: str, phrases: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z']){re.escape(p)}(?![a-z'])", text) for p in phrases)


def parse_yes_no(text: str) -> bool | None:
    """True for yes, False for no, None if unclear."""
    t = _basic_clean(text).replace("-", " ").replace(".", " ")
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    for w in DEFAULT_WAKE_WORDS:
        t = re.sub(rf"^{w}\b\s*", "", t)
    yes, no = _phrase_hit(t, YES_PHRASES), _phrase_hit(t, NO_PHRASES)
    if yes and not no:
        return True
    if no and not yes:
        return False
    return None  # nothing, or mixed signals ("yes no") - safer to ask again before printing


# ---------------------------------------------------------------- choices

CANCEL = "cancel"
_ORDINALS = [
    (0, ["first", "1st", "top one", "the top"]),
    (1, ["second", "2nd"]),
    (2, ["third", "3rd"]),
]
_CARDINALS = [
    (0, ["one", "1", "number one"]),
    (1, ["two", "2", "number two"]),
    (2, ["three", "3", "number three"]),
]


_CHOICE_FILLERS = {
    "the", "one", "from", "file", "that", "is", "by", "sent", "number", "this", "i", "want", "please", "print",
    "pick", "choose", "option", "it", "send", "to", "me",
}


def parse_choice(text: str, count: int, labels: list[str] | None = None) -> int | str | None:
    """Return a 0-based index, CANCEL, or None if unclear."""
    t = _basic_clean(text).replace("-", " ")
    t = re.sub(r"\.(?=\s|$)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    for w in DEFAULT_WAKE_WORDS:
        t = re.sub(rf"^{w}\b\s*", "", t)
    if t in STOP_PHRASES or _phrase_hit(t, ["cancel", "stop", "never mind", "nevermind", "none", "neither", "none of them"]):
        return CANCEL
    if _phrase_hit(t, ["last", "last one"]):
        return count - 1
    for idx, words in _ORDINALS:
        if idx < count and _phrase_hit(t, words):
            return idx
    if labels:
        # "the one from Juan" / "Maria's" / "the report"
        key = " ".join(w for w in t.split() if w not in _CHOICE_FILLERS)
        if len(key) >= 3:
            scores = [fuzz.partial_ratio(key, l.lower()) for l in labels[:count]]
            best = max(scores)
            if best >= 85 and scores.count(best) == 1:
                return scores.index(best)
    for idx, words in _CARDINALS:
        if idx < count and _phrase_hit(t, words):
            return idx
    if t in ("to", "too", "number to", "number too"):
        return 1 if count > 1 else None
    if t in ("won", "number won"):
        return 0
    return None


# ---------------------------------------------------------------- accounts

def match_account(spoken: str | None, accounts: dict, cutoff: int = 70):
    """Fuzzy-match a spoken account name to a configured account.

    ``accounts`` maps key -> object with .key/.email/.aliases. Returns the
    matched account or None.
    """
    if not spoken:
        return None
    target = squash(spoken)
    if not target:
        return None
    best_acct, best_score = None, 0.0
    for acct in accounts.values():
        terms = [acct.key, acct.email.split("@")[0], acct.email, getattr(acct, "name", ""), *acct.aliases]
        for term in terms:
            s = squash(term)
            if not s:
                continue
            score = max(fuzz.ratio(target, s), fuzz.token_set_ratio(spoken.lower(), term.lower()))
            if target == s:
                score = 100
            if score > best_score:
                best_acct, best_score = acct, score
    return best_acct if best_score >= cutoff else None


def account_terms(accounts: dict) -> list[str]:
    out = []
    for acct in accounts.values():
        out += [acct.key, acct.email.split("@")[0], getattr(acct, "name", ""), *acct.aliases]
    return out
