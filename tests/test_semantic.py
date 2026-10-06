"""Meaning-based matching with the real MiniLM model (skipped if the model isn't downloaded)."""
import pytest

from rika import semantic
from rika.intents import parse

TERMS = ["rjoprintingservice", "rjo", "rjo printing", "printing service", "shop", "RJO Printing Service"]


@pytest.fixture(scope="module")
def matcher():
    if semantic.model_dir() is None:
        pytest.skip("sentence model not downloaded (tools/download_models.py)")
    m = semantic.SemanticMatcher()
    assert m.load(allow_download=False)
    return m


@pytest.mark.parametrize(
    "text, intent",
    [
        # real phrases from rika.log that the rules alone missed
        ("is there documents I can print?", "list_files"),
        ("Scan for documents and PDFs.", "list_files"),
        ("There are documents in my Gmail.", "list_files"),
        ("All right, close the RJO printing service.", "close_gmail"),
        ("No, you must say you are Rika.", "about"),
        ("I must say your name is Rika.", "about"),
        # other everyday ways of saying things
        ("got any stuff for me to print", "list_files"),
        ("did a client email me something", "list_files"),
        ("what's waiting to be printed", "list_files"),
        ("check the inbox for attachments", "list_files"),
        ("we're closing, shut the gmail", "close_gmail"),
        ("close the email window please", "close_gmail"),
        ("bring the shop inbox up", "open_gmail"),
        ("I want to see the emails", "open_gmail"),
        ("does the printer work", "printer_status"),
        ("what are you good at", "help"),
        ("what kind of things can you help with", "help"),
        ("what do I call you", "about"),
        ("you did great", "thanks"),
        ("ok bye for now", "sleep"),
        ("that'll do for today", "sleep"),
        ("what's the temperature outside", "out_of_scope"),
        ("open netflix", "out_of_scope"),
    ],
)
def test_understands_meaning(matcher, text, intent):
    assert parse(text, account_terms=TERMS, semantic=matcher).name == intent


def test_fills_account_and_file(matcher):
    i = parse("can you get the shop inbox on screen", account_terms=TERMS, semantic=matcher)
    assert i.name == "open_gmail" and i.slots["account"] == "shop"
    i = parse("All right, close the RJO printing service.", account_terms=TERMS, semantic=matcher)
    assert i.name == "close_gmail" and i.slots["account"]


def test_rules_still_win_for_exact_commands(matcher):
    i = parse("print document one dot pdf", account_terms=TERMS, semantic=matcher)
    assert i.name == "print_file" and i.slots["filename"] == "document1.pdf"


SHOP_CHATTER = [
    "can I pay with gcash", "do you have A4 paper", "wait a moment", "the customer is here", "please sit down",
    "where is the stapler", "how many copies do you want", "make it colored", "black and white only",
    "that will be fifty pesos", "the ink is running low", "can you laminate this", "let me check", "hold on",
    "okay thank you ma'am", "next customer please", "Make it move forward.", "the cat is sleeping",
]


@pytest.mark.parametrize("text", SHOP_CHATTER)
def test_shop_chatter_never_opens_or_closes_anything(matcher, text):
    # (a stray print_file is harmless: she asks which file and always confirms before printing)
    assert parse(text, account_terms=TERMS, semantic=matcher).name not in ("open_gmail", "close_gmail", "list_files")


def test_never_closes_without_a_closing_word(matcher):
    for text in ["we're done for today", "no more customers", "that's it for the email"]:
        assert parse(text, account_terms=TERMS, semantic=matcher).name != "close_gmail", text


SLEEP_PHRASES = [
    "Rika, sleep.", "Rika sleep", "Rikka, go to sleep.", "Rika, you can rest now.", "You can rest now.", "Rest now, Rika.",
    "Rika, take a rest.", "Go take a nap.", "Rika, nap time.", "Time to sleep, Rika.", "Okay Rika, you can go to sleep now.",
    "That's enough for today, Rika.", "You can go now.", "Rika, take a break.", "Rika, you're done for today.",
    "Good night, Rika.", "Rika, rest.", "Rika, power down.", "Go back to sleep.", "Rika, standby.",
    "I don't need you anymore, thanks.", "Sleep mode.", "Rika, turn off.", "You may rest.", "Rika, relax.",
    "Rika, that's it for now.", "Rest up.", "Get some sleep.", "We're done here.", "Rika, go to bed.", "Bye bye Rika!",
    "Catch you later.", "Rika, you can sleep now, thank you.", "Rika, you deserve a rest.", "Shut down, Rika.",
    "Rika, you can stop listening now.", "Rika, call it a day.", "Rika, off you go.", "Thanks Rika, I'm all set.",
]


@pytest.mark.parametrize("text", SLEEP_PHRASES)
def test_sleep_by_meaning(matcher, text):
    assert parse(text, account_terms=TERMS, semantic=matcher).name == "sleep"


@pytest.mark.parametrize(
    "text, intent",
    [("Is the printer sleeping?", "printer_status"), ("print the rest of the files", "print_file"),
     ("open the gmail and then rest", "open_gmail"), ("what's your name, Rika?", "about"),
     ("turn off the lights", "out_of_scope"), ("shut down chrome", "close_gmail")],
)
def test_sleep_words_in_other_commands(matcher, text, intent):
    assert parse(text, account_terms=TERMS, semantic=matcher).name == intent
