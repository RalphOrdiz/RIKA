"""Example phrasings for each intent - the meaning-based matcher compares what you said to these.

Add more lines here whenever Rika misunderstands something you say often; no code changes needed.
The rules in intents.py still run first (they pull out file names and account names);
these examples catch everything the rules don't.
"""
from __future__ import annotations

EXAMPLES: dict[str, list[str]] = {
    "list_files": [
        "are there files to print", "is there anything to print", "do I have documents to print",
        "are there documents I can print", "is there something I need to print", "any printable files in my gmail",
        "check gmail for files", "scan for documents and pdfs", "look for attachments in my email",
        "did anyone send me a file", "did a customer send something", "what files came in", "show me the new files",
        "what documents do I have", "any new pdfs", "check if there are new attachments", "what can I print",
        "is there a print job waiting", "did I receive any documents", "what's in my inbox to print",
        "there are documents in my gmail", "find files in my email", "list the attachments", "any word files",
        "what did customers send today", "check for new orders to print", "do we have anything to print today",
        "see if there are files", "are there any new uploads", "search my email for documents",
    ],
    "print_file": [
        "print the file", "print this document", "please print it out", "send it to the printer",
        "make a copy of the document", "print the attachment", "print the pdf", "can you print a file for me",
        "I need a printout", "run it through the printer", "print the latest file", "print the newest document",
        "get me a hard copy", "print the word file", "put the file on paper",
    ],
    "open_gmail": [
        "open gmail", "open the shop email", "show me the inbox", "bring up the gmail", "open my email",
        "launch gmail", "go to the inbox", "pull up the rjo email", "open the printing service account",
        "let me see the emails", "can I see my inbox", "start gmail", "put gmail on the screen", "open the mail",
        "take me to my email",
    ],
    "close_gmail": [
        "close gmail", "close the browser", "close chrome", "shut the email", "close the inbox", "close it",
        "close the shop email", "we're done close everything", "exit the browser", "hide the email",
        "close the rjo printing service", "close the window", "get rid of the gmail window", "close all of it",
        "no more customers close the email", "shut down chrome", "we are closing now close gmail",
    ],
    "printer_status": [
        "is the printer ready", "is the printer on", "is the printer working", "check the printer",
        "what printer do you use", "is the printer connected", "is the printer online", "printer status",
        "can the printer print right now", "is the printer okay", "does the printer have a problem",
    ],
    "help": [
        "what can you do", "what are you capable of", "how do I use you", "help me", "what commands do you know",
        "what are your features", "what can I ask you", "how does this work", "what do you do",
        "teach me how to use you", "what are your skills", "what jobs can you do", "how can you help me",
        "what should I say to you", "explain what you can do",
    ],
    "about": [
        "who are you", "what do people call you", "what is your name", "what's your name", "tell me about yourself", "introduce yourself",
        "what are you", "are you a robot", "who made you", "what should I call you", "say your name",
        "tell me your name", "you are called", "say your name", "say who you are", "you must say your name",
    ],
    "how_are_you": [
        "how are you", "how are you doing", "how's it going", "are you okay", "how do you feel",
        "how's your day", "are you doing well", "how have you been",
    ],
    "greeting": ["hello", "hi there", "good morning", "good afternoon", "good evening", "hey there", "hiya"],
    "thanks": [
        "thank you", "thanks", "thanks a lot", "thank you so much", "appreciate it", "great job", "good job",
        "nice work", "you're the best", "well done", "you did a great job", "awesome work", "perfect, thanks",
        "you were very helpful",
    ],
    "sleep": [
        "that's all", "that is all for now", "go to sleep", "goodbye", "bye", "I'm done", "nothing else",
        "you can rest now", "see you later", "that's it for today", "we're finished", "no more for now",
        "take a break", "good night", "you can rest now", "rest now", "time to sleep", "power down", "standby",
        "turn off now", "relax for a while", "I don't need you anymore", "that's enough for today", "go to bed",
        "you can take a nap", "sleep mode", "stop listening", "we're done here", "you're off duty",
        "call it a day", "off you go", "I'm all set", "rest your voice", "you deserve a rest", "go rest for a bit",
    ],
    "stop": ["stop", "cancel", "never mind", "forget it", "stop talking", "be quiet", "abort that", "cancel that"],
    # Things Rika can't do - matched so she says "I can only open Gmail and print" instead of guessing.
    "out_of_scope": [
        "what's the weather", "tell me a joke", "play some music", "what time is it", "send an email",
        "reply to the customer", "delete the email", "set an alarm", "call someone", "what's the news",
        "order food", "how much does printing cost", "open youtube", "search the internet", "translate this",
        "do my homework", "write a letter", "what is the capital of france", "turn off the lights",
        "open facebook", "scan a document with the scanner", "make a phone call", "what day is it",
        "who is the president", "sing a song", "calculate the total", "book a ride",
    ],
}
