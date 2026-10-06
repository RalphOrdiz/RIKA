import pytest

from rika.config import Account
from rika.intents import CANCEL, clean_utterance, match_account, parse, parse_choice, parse_yes_no

ACCOUNTS = {
    "rjoprintingservice": Account(
        "rjoprintingservice", "rjoprintingservice@gmail.com", "Profile 2",
        ["rjo", "rjo printing", "printing service", "shop"], "RJO Printing Service",
    ),
    "personal": Account("personal", "juan.delacruz@gmail.com", "Default", ["juan", "my personal"], "Personal"),
}
TERMS = ["rjoprintingservice", "rjo", "rjo printing", "printing service", "shop", "personal", "juan"]


# ------------------------------------------------------------ print

@pytest.mark.parametrize(
    "text, filename",
    [
        ("Rika, print document1.pdf", "document1.pdf"),
        ("Rika print document1.pdf.", "document1.pdf"),
        ("rika, print document one dot pdf", "document1.pdf"),
        ("Print document 1.PDF", "document1.pdf"),
        ("print the file called report dot docx", "report.docx"),
        ("Hey Rika, can you please print the file called final report dot docx for me?", "final report.docx"),
        ("Rika could you print document1.pdf please", "document1.pdf"),
        ("please print invoice twenty twenty four dot pdf", "invoice2024.pdf"),
        ("print out document1.pdf", "document1.pdf"),
        ("Rika, help me print report.docx", "report.docx"),
        ("I want to print resume.pdf", "resume.pdf"),
        ("I need to print my underscore resume dot pdf", "my_resume.pdf"),
        ("Okay Rika, print id dash front dot pdf", "id-front.pdf"),
        ("Rika, would you print module three dot pdf now", "module3.pdf"),
        ("Rika, print the attachment named scan zero zero one dot pdf", "scan001.pdf"),
        ("let's print thesis final docx", "thesis final.docx"),
        ("document1.pdf, print it", "document1.pdf"),
        ("Rica, print form one dot pdf from gmail", "form1.pdf"),
        # mis-heard verb
        ("Prince document one dot pdf", "document1.pdf"),
        ("prints document1.pdf", "document1.pdf"),
        # no file name yet
        ("Rika, print", ""),
        ("print it", ""),
        ("print something", "something"),
    ],
)
def test_print_intent(text, filename):
    intent = parse(text, account_terms=TERMS)
    assert intent.name == "print_file", text
    assert intent.slots["filename"] == filename


# ------------------------------------------------------------ open gmail

@pytest.mark.parametrize(
    "text, account",
    [
        ("Rika, open rjoprintingservice gmail", "rjoprintingservice"),
        ("Rika, open rjoprintingservice gmail.", "rjoprintingservice"),
        ("open RJO Printing Service gmail", "rjo printing service"),
        ("Rika open the gmail of RJO printing service", "rjo printing service"),
        ("open the RJO email", "rjo"),
        ("open rjo g-mail", "rjo"),
        ("open rjo g mail", "rjo"),
        ("open shop inbox", "shop"),
        ("can you open my personal gmail", "personal"),
        ("open rjoprintingservice@gmail.com", "rjoprintingservice"),
        ("go to rjo gmail", "rjo"),
        ("check the rjo gmail", "rjo"),
        ("Rika, pull up our shop email", "shop"),
        ("open gmail", None),
        ("Rika, open Gmail please", None),
        ("show me my email", None),
        # no "gmail" word but a known account
        ("open rjo", "rjo"),
        ("opn rjo gmail", "rjo"),
    ],
)
def test_open_gmail_intent(text, account):
    intent = parse(text, account_terms=TERMS)
    assert intent.name == "open_gmail", text
    assert intent.slots["account"] == account


def test_open_unknown_thing_is_not_gmail():
    assert parse("open the window", account_terms=TERMS).name == "unknown"


# ------------------------------------------------------------ stop / help / other

@pytest.mark.parametrize(
    "text",
    ["Rika, stop", "﻿Rika, stop", "stop", "Stop.", "cancel", "Rika cancel that", "never mind", "nevermind",
     "stop it", "forget it", "shut up", "be quiet", "okay stop", "stop talking", "abort"],
)
def test_stop(text):
    assert parse(text).name == "stop"


@pytest.mark.parametrize(
    "text",
    ["Rika, what can you do?", "what can you do", "help", "Rika help", "what are your commands",
     "what can I say", "how do I use you", "list commands", "what else can you do"],
)
def test_help(text):
    assert parse(text).name == "help"


@pytest.mark.parametrize("text", ["Rika", "Rika.", "Hey Rika", "Rica!", "okay rika"])
def test_wake_only(text):
    assert parse(text).name == "wake_only"


@pytest.mark.parametrize(
    "text",
    ["", "asdf qwerty", "banana pancakes",
     # English only: Tagalog/Bisaya commands are no longer understood
     "buksan mo yung gmail ng rjo", "tama na", "ano kaya mo"],
)
def test_unknown(text):
    assert parse(text, account_terms=TERMS).name == "unknown"


# ------------------------------------------------------------ conversation

@pytest.mark.parametrize(
    "text",
    ["Rika, do you see a printable file in my gmail?", "Any files to print?", "Is there anything to print?",
     "what can I print", "did someone send me a pdf", "check for new documents", "Rika, any new attachments?",
     "do I have files waiting", "show me the files", "what documents do I have", "are there any print jobs"],
)
def test_list_files(text):
    assert parse(text, account_terms=TERMS).name == "list_files"


@pytest.mark.parametrize(
    "text, filename",
    [("do you see report.pdf in my gmail", "report.pdf"), ("Find resume dot pdf", "resume.pdf"),
     ("did someone send me invoice one dot docx", "invoice1.docx"), ("look for the file called thesis.docx", "thesis.docx"),
     ("Rika, print the new file report.pdf", "report.pdf")],
)
def test_find_named_file_goes_to_print(text, filename):
    intent = parse(text, account_terms=TERMS)
    assert intent.name == "print_file" and intent.slots["filename"] == filename


@pytest.mark.parametrize(
    "text, name",
    [
        ("Rika, what are you capable of?", "help"), ("what are you capable", "help"), ("what can you help me with", "help"),
        ("how do I print", "help"), ("how does this work", "help"),
        ("hello", "greeting"), ("Good morning Rika", "greeting"), ("hi there", "greeting"),
        ("thanks", "thanks"), ("thank you so much", "thanks"), ("Thank you Rika", "thanks"),
        ("who are you", "about"), ("what's your name", "about"), ("tell me about yourself", "about"),
        ("how are you", "how_are_you"), ("how's it going", "how_are_you"),
        ("printer status", "printer_status"), ("is the printer ready?", "printer_status"),
        ("which printer do you use", "printer_status"), ("is the printer on", "printer_status"),
        ("that's all", "sleep"), ("Thank you Rika, that's all", "sleep"), ("go to sleep", "sleep"), ("bye Rika", "sleep"),
        ("no that's it", "sleep"), ("goodbye", "sleep"), ("I'm done", "sleep"), ("nothing else thanks", "sleep"),
        ("Rika, what's the weather", "out_of_scope"), ("tell me a joke", "out_of_scope"), ("play some music", "out_of_scope"),
        ("send an email to Juan", "out_of_scope"), ("delete that email", "out_of_scope"), ("what time is it", "out_of_scope"),
        ("can you order pizza?", "out_of_scope"), ("why is the sky blue", "out_of_scope"),
    ],
)
def test_conversation_intents(text, name):
    assert parse(text, account_terms=TERMS).name == name


def test_clean_utterance():
    assert clean_utterance("Hey Rika, can you please print it?") == "print it"
    assert clean_utterance("Rikka, open gmail.") == "open gmail"
    assert clean_utterance("Okay Rica. print x.pdf") == "print x.pdf"
    assert clean_utterance("Oh, well, Rika, help") == "help"


# ------------------------------------------------------------ yes / no

@pytest.mark.parametrize(
    "text",
    ["yes", "Yes.", "yeah", "Yeah!", "yep", "Yep.", "sure", "Sure!", "go", "Go.", "print it", "Print it!",
     "yes please", "Yes, print it", "yup", "okay", "ok", "go ahead", "do it", "of course", "absolutely",
     "Rika yes", "Yeah, sure."],
)
def test_yes(text):
    assert parse_yes_no(text) is True


@pytest.mark.parametrize(
    "text",
    ["no", "No.", "nope", "Nope!", "cancel", "Cancel.", "stop", "Stop!", "nah", "don't", "do not print",
     "no thanks", "never mind", "not yet", "wait", "Oh no", "No, cancel it."],
)
def test_no(text):
    assert parse_yes_no(text) is False


@pytest.mark.parametrize(
    "text",
    ["", "hmm", "what", "maybe", "the printer", "yes no", "no go",
     # English only: these used to be accepted, now Rika asks again
     "oo", "sige", "opo", "dili", "ayaw", "hindi"],
)
def test_yes_no_unclear(text):
    assert parse_yes_no(text) is None


# ------------------------------------------------------------ choices

LABELS = ["report.pdf Pedro Santos", "report.pdf Juan Dela Cruz", "report.docx Maria Clara"]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("first", 0), ("the first one", 0), ("First.", 0), ("number one", 0), ("one", 0), ("1", 0), ("the top one", 0),
        ("second", 1), ("the second one", 1), ("two", 1), ("2", 1), ("2nd", 1), ("to", 1),
        ("third", 2), ("the third one", 2), ("number three", 2), ("3", 2), ("the last one", 2),
        ("cancel", CANCEL), ("stop", CANCEL), ("none of them", CANCEL), ("never mind", CANCEL),
        ("the one from Juan", 1), ("the one Maria sent", 2), ("Pedro", 0),
        ("banana", None), ("", None), ("pangalawa", None),
    ],
)
def test_parse_choice(text, expected):
    assert parse_choice(text, 3, LABELS) == expected


def test_choice_respects_count():
    assert parse_choice("third", 2) is None
    assert parse_choice("last", 2) == 1


# ------------------------------------------------------------ account matching

@pytest.mark.parametrize(
    "spoken, key",
    [
        ("rjoprintingservice", "rjoprintingservice"),
        ("rjo printing service", "rjoprintingservice"),
        ("RJO", "rjoprintingservice"),
        ("r j o", "rjoprintingservice"),
        ("our jo printing service", "rjoprintingservice"),
        ("rjo printing servic", "rjoprintingservice"),
        ("shop", "rjoprintingservice"),
        ("personal", "personal"),
        ("juan", "personal"),
        ("juan.delacruz@gmail.com", "personal"),
    ],
)
def test_match_account(spoken, key):
    acct = match_account(spoken, ACCOUNTS)
    assert acct is not None and acct.key == key


@pytest.mark.parametrize("spoken", ["banana", "facebook", "", None])
def test_match_account_none(spoken):
    assert match_account(spoken, ACCOUNTS) is None
