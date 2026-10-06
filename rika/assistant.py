"""Rika's brain: parse an utterance, run the action, handle errors in a friendly way."""
from __future__ import annotations

import logging
from typing import Callable

from rika import gmail_auth
from rika.actions import gmail_open, gmail_print
from rika.config import Config
from rika.errors import Cancelled, GmailNotConfigured, GmailTokenExpired, RikaError, AccountNotFound
from rika.intents import DEFAULT_WAKE_WORDS, Intent, account_terms, match_account, parse, parse_yes_no
from rika.interaction import Interaction
from rika.lines import join_names, line
from rika.mailbox import GmailMailbox, Mailbox

log = logging.getLogger(__name__)


class Assistant:
    def __init__(
        self,
        cfg: Config,
        io: Interaction,
        mailbox_factory: Callable[[], Mailbox] | None = None,
        semantic=None,
        dry_run: bool = False,
    ):
        self.cfg = cfg
        self.io = io
        self.dry_run = dry_run
        self._mailbox_factory = mailbox_factory
        self._mailbox: Mailbox | None = None
        self.semantic = semantic  # optional meaning matcher (rika.semantic.SemanticMatcher)
        self.interrupted = False  # set by the voice controller when "stop" cut something off

    @property
    def wake_words(self) -> list[str]:
        return list(dict.fromkeys(self.cfg.wake_words + DEFAULT_WAKE_WORDS))

    def mailbox(self) -> Mailbox:
        if self._mailbox is None:
            if self._mailbox_factory:
                self._mailbox = self._mailbox_factory()
            else:
                acct = self.cfg.print_account
                self._mailbox = GmailMailbox(acct.email, gmail_auth.load_credentials)
        return self._mailbox

    # ------------------------------------------------------------ entry point
    def parse(self, text: str):
        return parse(text, self.wake_words, account_terms(self.cfg.accounts), semantic=self.semantic)

    def handle(self, text: str) -> Intent:
        intent = self.parse(text)
        log.info("Heard %r -> %s %s", text, intent.name, intent.slots)
        try:
            self._dispatch(intent)
        except Cancelled:
            log.info("Action cancelled")
            if not self.io.cancel_event.is_set():
                self.io.say(line("cancelled"))
        except (GmailTokenExpired, GmailNotConfigured) as exc:
            log.error("%s: %s", type(exc).__name__, exc)
            self._mailbox = None
            self._say_error(exc)
            self._offer_reconnect()
        except RikaError as exc:
            log.error("%s: %s", type(exc).__name__, exc)
            self._say_error(exc)
        except Exception:
            log.exception("Unexpected error while handling %r", text)
            self._say_error(RikaError("unexpected"))
        finally:
            self.interrupted = False
        return intent

    def _say_error(self, exc: RikaError) -> None:
        try:
            self.io.say(line(exc.line_key, **exc.fmt))
        except Cancelled:
            pass

    def _dispatch(self, intent: Intent) -> None:
        io = self.io
        if intent.name == "stop":
            io.say(line("stop_sleep"))
        elif intent.name == "sleep":
            io.say(line("sleep"))
        elif intent.name == "help":
            io.say(line("help"))
        elif intent.name in ("greeting", "thanks", "about", "how_are_you", "out_of_scope"):
            io.say(line(intent.name))
        elif intent.name == "list_files":
            gmail_print.run_list(self)
        elif intent.name == "printer_status":
            self.printer_status()
        elif intent.name == "wake_only":
            reply = io.ask(line("wake"))
            if reply:
                self._dispatch(self.parse(reply))
            else:
                io.say(line("sleep"))
        elif intent.name == "open_gmail":
            self.open_gmail(intent.slots.get("account"))
        elif intent.name == "close_gmail":
            self.close_gmail(intent.slots.get("account"))
        elif intent.name == "print_file":
            gmail_print.run_print(self, intent.slots.get("filename", ""))
        else:
            io.say(line("not_understood"))

    # ------------------------------------------------------------ helpers
    def ask_yes_no(self, question: str) -> bool:
        prompt = question
        for _ in range(3):
            reply = self.io.ask(prompt, hint="Yes. No. Yeah. Nope. Sure. Cancel.")
            answer = parse_yes_no(reply or "")
            if answer is not None:
                return answer
            prompt = line("confirm_again")
        return False

    def printer_status(self) -> None:
        from rika.actions import printer as prn

        name, _ = prn.resolve_printer(self.cfg.printer)  # raises PrinterNotFound -> friendly line
        prn.check_printer_ready(name)  # raises PrinterOffline -> friendly line
        self.io.say(line("printer_ready", printer=prn.short_printer_name(name)))

    def open_gmail(self, spoken_account: str | None) -> None:
        accounts = self.cfg.accounts
        names = join_names([a.spoken_name for a in accounts.values()])
        if spoken_account:
            acct = match_account(spoken_account, accounts)
            if acct is None:
                raise AccountNotFound(f"No account matches {spoken_account!r}", accounts=names)
        elif len(accounts) == 1:
            acct = next(iter(accounts.values()))
        else:
            reply = self.io.ask(line("which_account", accounts=names))
            acct = match_account(reply, accounts)
            if acct is None:
                raise AccountNotFound(f"No account matches {reply!r}", accounts=names)
        gmail_open.open_gmail(
            acct.email, acct.chrome_profile_dir, self.cfg.get("chrome_path", ""), self.dry_run,
            maximize=bool(self.cfg.get("maximize_gmail", True)),
        )
        self._last_opened = acct.key
        self.io.say(line("opening_gmail", account=acct.spoken_name))

    def close_gmail(self, spoken_account: str | None) -> None:
        """Close only that account's Chrome window(s) - other Chrome windows stay open."""
        accounts = self.cfg.accounts
        if spoken_account:
            acct = match_account(spoken_account, accounts)
            if acct is None:
                names = join_names([a.spoken_name for a in accounts.values()])
                raise AccountNotFound(f"No account matches {spoken_account!r}", accounts=names)
        else:
            # "close it" -> the one Rika opened last, else whichever account has a window open
            acct = accounts.get(getattr(self, "_last_opened", "")) or next(
                (a for a in accounts.values() if gmail_open.account_windows(a.email)), None
            ) or self.cfg.print_account
        closed = gmail_open.close_gmail(acct.email, self.dry_run)
        self.io.say(line("closing_gmail" if closed else "gmail_not_open", account=acct.spoken_name))

    def _offer_reconnect(self) -> None:
        if not gmail_auth.has_credentials_file():
            return  # needs SETUP.md steps first; nothing to click yet
        try:
            if not self.ask_yes_no(line("reconnect_offer")):
                return
            self.io.say(line("reconnect_wait"))
            acct = self.cfg.print_account
            gmail_auth.run_auth_flow(login_hint=acct.email if acct else None)
            self._mailbox = None
            self.io.say(line("reconnected"))
        except Cancelled:
            pass
        except Exception as exc:
            log.error("Gmail reconnect failed: %s", exc)
            self._say_error(RikaError(str(exc)))
