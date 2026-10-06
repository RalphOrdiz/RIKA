"""Turn a spoken / transcribed file name into a real file name.

Examples::

    "document one dot pdf"                -> "document1.pdf"
    "Document 1.PDF."                     -> "document1.pdf"
    "my underscore resume dash final dot doc x" -> "my_resume-final.docx"
    "invoice twenty twenty four dot pdf"  -> "invoice2024.pdf"
    "report docx"                         -> "report.docx"
"""
from __future__ import annotations

import re
from dataclasses import dataclass

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9}
TEENS = {
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1000}
NUMBER_WORDS = set(UNITS) | set(TEENS) | set(TENS) | set(SCALES)
# Homophones Whisper likes to produce, only trusted right before ".ext"
HOMOPHONES = {"for": "4", "to": "2", "too": "2", "won": "1", "ate": "8"}

SYMBOL_WORDS = {
    "dot": ".", "point": ".", "period": ".",
    "underscore": "_", "underscores": "_",
    "dash": "-", "hyphen": "-", "minus": "-",
}
EXTENSIONS = {"pdf": "pdf", "docx": "docx", "doc": "doc"}
EXT_ALIASES = {
    "pdf": "pdf", "pdef": "pdf", "pdfs": "pdf",
    "docx": "docx", "docks": "docx", "dox": "docx", "doxx": "docx", "docxs": "docx",
    "doc": "doc", "dock": "doc", "docs": "doc",
}
LEADING_FILLERS = [
    "the file called", "the file named", "the attachment called", "the attachment named",
    "file called", "file named", "attachment called", "attachment named", "a file called",
    "the new file called", "the new file named", "the new file", "new file", "the latest file", "latest file",
    "the newest file", "newest file", "the pdf called", "the pdf named", "the document called", "the document named",
    "the attachment", "the file", "the", "a", "an", "my", "this", "that",
]
TRAILING_FILLERS = [
    "from my gmail", "from the gmail", "from gmail", "in my gmail", "in gmail", "from email",
    "from my email", "in my email", "for me", "right now", "now", "please", "pls",
    "thanks", "thank you", "file", "attachment",
]


@dataclass
class _Tok:
    text: str
    kind: str  # "word" | "num" | "sym" | "space"
    space_before: bool


def _strip_fillers(text: str) -> str:
    changed = True
    while changed and text:
        changed = False
        for f in LEADING_FILLERS:
            if text == f or text.startswith(f + " "):
                rest = text[len(f):].strip()
                first = rest.split(" ", 1)[0] if rest else ""
                if first in SYMBOL_WORDS or first in ("under", "_", "-", "."):
                    break  # "my underscore resume" - "my" is part of the name
                text = rest
                changed = True
                break
        for f in TRAILING_FILLERS:
            if text == f or text.endswith(" " + f):
                text = text[: len(text) - len(f)].strip()
                changed = True
                break
    return text


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    for m in re.finditer(r"[a-z]+|\d+|[._\-]", text):
        start = m.start()
        space = start > 0 and text[start - 1].isspace()
        val = m.group()
        if val.isdigit():
            kind = "num"
        elif val in "._-":
            kind = "sym"
        else:
            kind = "word"
        toks.append(_Tok(val, kind, space))
    return toks


def _words_to_number(words: list[str]) -> str:
    """Convert a run of number words to a digit string, concatenating groups.

    "one two three" -> "123", "twenty twenty four" -> "2024", "one hundred five" -> "105".
    """
    out: list[str] = []
    total = part = 0
    last: str | None = None  # unit|teen|tens|hundred|thousand|zero

    def flush() -> None:
        nonlocal total, part, last
        if last is not None:
            out.append("0" if last == "zero" else str(total + part))
        total = part = 0
        last = None

    for w in words:
        if w == "zero":
            flush()
            out.append("0")
            continue
        if w in UNITS:
            v = UNITS[w]
            attach = last in ("hundred", "thousand") or (last == "tens" and part % 10 == 0)
            if not attach:
                flush()
            part += v
            last = "unit"
        elif w in TEENS:
            if last not in ("hundred", "thousand"):
                flush()
            part += TEENS[w]
            last = "teen"
        elif w in TENS:
            if last not in ("hundred", "thousand"):
                flush()
            part += TENS[w]
            last = "tens"
        elif w == "hundred":
            if last in ("unit", "teen", "tens") and part < 100:
                part *= 100
            else:
                flush()
                part = 100
            last = "hundred"
        elif w == "thousand":
            if last in ("unit", "teen", "tens", "hundred") and total == 0:
                total = part * 1000
                part = 0
            else:
                flush()
                total = 1000
            last = "thousand"
    flush()
    return "".join(out)


def _rewrite(toks: list[_Tok]) -> list[_Tok]:
    """Map spoken symbols, extensions and numbers onto real tokens."""
    out: list[_Tok] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        if t.kind == "word":
            # "under score" -> "_"
            if t.text == "under" and nxt and nxt.text == "score":
                out.append(_Tok("_", "sym", t.space_before))
                i += 2
                continue
            if t.text in SYMBOL_WORDS:
                out.append(_Tok(SYMBOL_WORDS[t.text], "sym", t.space_before))
                i += 1
                continue
            if t.text == "space":
                out.append(_Tok(" ", "space", t.space_before))
                i += 1
                continue
            if t.text in NUMBER_WORDS:
                run = []
                j = i
                while j < len(toks) and toks[j].kind == "word" and toks[j].text in NUMBER_WORDS:
                    run.append(toks[j].text)
                    j += 1
                digits = _words_to_number(run)
                out.append(_Tok(digits, "num", t.space_before))
                i = j
                continue
            if t.text in HOMOPHONES and nxt and nxt.text in (".", "dot", "point") and out and out[-1].kind == "word":
                out.append(_Tok(HOMOPHONES[t.text], "num", t.space_before))
                i += 1
                continue
        out.append(t)
        i += 1
    return out


def _fix_extension(toks: list[_Tok]) -> list[_Tok]:
    """Normalise the extension at the end: '. doc x' -> '.docx', trailing 'pdf' -> '.pdf'."""
    if not toks:
        return toks
    # "doc x" / "doc s" spelled at the end
    if len(toks) >= 2 and toks[-2].text in ("doc", "dock") and toks[-1].text in ("x", "ex", "s"):
        toks = toks[:-2] + [_Tok("docx", "word", toks[-2].space_before)]
    # spelled "p d f" -> already glued by letter-run handling later; handle here explicitly
    if len(toks) >= 3 and [t.text for t in toks[-3:]] == ["p", "d", "f"]:
        toks = toks[:-3] + [_Tok("pdf", "word", toks[-3].space_before)]
    last = toks[-1]
    if last.kind == "word" and last.text in EXT_ALIASES:
        ext = EXT_ALIASES[last.text]
        has_dot = len(toks) >= 2 and toks[-2].text == "."
        if has_dot:
            toks = toks[:-1] + [_Tok(ext, "word", False)]
        elif len(toks) >= 2 and last.text in ("pdf", "docx", "doc", "pdef", "docks", "dox"):
            # "report pdf" -> "report.pdf" (only the unambiguous spellings without a dot)
            toks = toks[:-1] + [_Tok(".", "sym", False), _Tok(ext, "word", False)]
    return toks


def _join(toks: list[_Tok]) -> str:
    parts: list[str] = []
    prev: _Tok | None = None
    for i, t in enumerate(toks):
        if t.kind == "space":
            parts.append(" ")
            prev = t
            continue
        if prev is None or prev.kind == "space":
            parts.append(t.text)
        elif t.kind == "sym" or prev.kind == "sym":
            parts.append(t.text)
        elif t.kind == "num" and prev.kind in ("word", "num"):
            parts.append(t.text)  # "document 1" -> "document1"
        elif (
            t.kind == "word" and prev.kind == "word" and len(t.text) == 1 and len(prev.text) == 1
        ):
            parts.append(t.text)  # spelled letters "j u a n" -> "juan"
        else:
            parts.append((" " if t.space_before else "") + t.text)
        prev = t
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def normalize_filename(spoken: str) -> str:
    """Normalise a spoken file name. Returns '' if nothing usable is left."""
    text = spoken.lower()
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[\"'`,!?;:()\[\]{}]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[.\s]+$", "", text)  # trailing sentence period(s)
    text = _strip_fillers(text)
    toks = _tokenize(text)
    toks = _rewrite(toks)
    toks = _fix_extension(toks)
    # collapse repeated symbols / leading-trailing junk
    name = _join(toks)
    name = re.sub(r"\.{2,}", ".", name)
    name = re.sub(r"\s*([._-])\s*", r"\1", name)
    return name.strip(" ._-")


def split_ext(name: str) -> tuple[str, str]:
    """('document1', '.pdf') - extension only if it is one of ours."""
    m = re.match(r"^(.*?)(\.(?:pdf|docx|doc))$", name.lower())
    if m:
        return m.group(1), m.group(2)
    return name.lower(), ""


def squash(text: str) -> str:
    """Lowercase alphanumerics only - for forgiving comparisons."""
    return re.sub(r"[^a-z0-9]", "", text.lower())
