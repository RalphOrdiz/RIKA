import json
import re
import threading
from pathlib import Path

import pytest

from rika import config as config_mod
from rika import tts
from rika.lines import LINES, LIVE_FIELDS, fixed_lines, prerender_lines

# ------------------------------------------------------------ helpers


def test_speakable_strips_tilde():
    assert tts.speakable("Printing now~!") == "Printing now!"
    assert tts.speakable("  Hi   hi!  ") == "Hi hi!"


def test_split_chunks():
    text = "Print document1.pdf from Juan Dela Cruz, 3 pages, on EPSON L120? Yes or no?"
    chunks = tts.split_chunks(text)
    assert " ".join(chunks) == text
    assert chunks[-1] == "Yes or no?"
    assert len(chunks) >= 3
    assert tts.split_chunks("All done!") == ["All done!"]


def test_parse_and_label_blend():
    assert tts.parse_blend("af_bella:0.6,af_nicole:0.4") == [["af_bella", 0.6], ["af_nicole", 0.4]]
    assert tts.parse_blend(" af_sky ") == [["af_sky", 1.0]]
    assert tts.normalize_blend([["a", 3], ["b", 1]]) == [["a", 0.75], ["b", 0.25]]
    assert tts.blend_label([["af_bella", 0.7], ["af_nicole", 0.3]]) == "af_bella 70% + af_nicole 30%"
    assert tts.blend_label([["af_sky", 1]]) == "af_sky"
    with pytest.raises(ValueError):
        tts.parse_blend("")


def test_blend_lang():
    assert tts.blend_lang([["af_bella", 0.7], ["bf_emma", 0.3]]) == "en-us"
    assert tts.blend_lang([["af_bella", 0.3], ["bf_emma", 0.7]]) == "en-gb"


def test_blend_style_is_weighted_average():
    import numpy as np

    class FakeKokoro:
        styles = {"af_a": np.ones((2, 3), np.float32), "af_b": np.zeros((2, 3), np.float32)}

        def get_voices(self):
            return list(self.styles)

        def get_voice_style(self, name):
            return self.styles[name]

    style = tts.blend_style(FakeKokoro(), [["af_a", 0.75], ["af_b", 0.25]])
    assert np.allclose(style, 0.75)
    with pytest.raises(ValueError, match="Unknown Kokoro voice"):
        tts.blend_style(FakeKokoro(), [["af_nope", 1]])


# ------------------------------------------------------------ lines

def test_lines_are_english_only():
    banned = re.compile(r"(?i)\b(gomen|na po|po\b|sige|ganbatte|hai|oo|dili|ayaw|i-print|yung)\b")
    for key, pool in LINES.items():
        assert pool, key
        for text in pool:
            assert not banned.search(text), (key, text)


def test_spec_examples_present():
    assert "Hi hi! I'm listening!" in LINES["wake"]
    assert "Sorry, can you say that again?" in LINES["not_understood"]
    assert "Printing now~!" in LINES["printing"]
    assert "All done!" in LINES["done"]
    assert "Hmm, I can't find that file..." in LINES["not_found"]


def test_prerender_expands_config_values_but_not_names():
    values = {"account": ["RJO Printing Service"], "accounts": ["RJO Printing Service"], "printer": ["EPSON L120"],
              "days": ["14"], "count": ["2", "3"]}
    out = prerender_lines(values)
    assert set(fixed_lines()) <= set(out)
    assert "Opening RJO Printing Service Gmail now!" in out
    assert "I found 3 files!" in out
    assert not any("{" in t for t in out)
    # lines with a file or sender name are generated live, never pre-rendered
    assert {"name", "sender"} <= LIVE_FIELDS
    live_starts = ("Print ", "Okay, looking for", "Searching Gmail for", "Let me find", "First:", "The closest")
    assert not any(o.startswith(live_starts) for o in out)


# ------------------------------------------------------------ config

V1 = {
    "accounts": {"shop": {"email": "shop@gmail.com", "chrome_profile_dir": "Default"}},
    "default_print_account": "shop",
    "printer": "EPSON L120 Series",
    "tts": {"engine": "edge", "voice": "en-US-JennyNeural", "pitch": "+10Hz", "rate": "+5%", "volume": "+0%",
            "sapi_voice": "Zira", "piper_exe": "", "piper_model": ""},
    "stt": {"model": "base.en", "language": "en", "beam_size": 5},
}


def test_migrates_v1_config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(V1), encoding="utf-8")
    cfg = config_mod.load_config(path)
    assert cfg.problems == []
    assert cfg.tts["engine"] == "kokoro"
    assert cfg.tts["voices"] == [["af_bella", 0.7], ["af_nicole", 0.3]]
    assert cfg.tts["edge"]["voice"] == "en-US-JennyNeural"
    assert cfg.tts["edge"]["pitch"] == "+10Hz"
    assert cfg.tts["fallback"] == ["edge", "sapi"]
    assert "language" not in cfg.stt
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config_version"] == 2 and "voice" not in saved["tts"]
    assert (tmp_path / "config.v1.backup.json").exists()
    assert cfg.accounts["shop"].email == "shop@gmail.com"


@pytest.mark.parametrize(
    "tts_patch, bad_key",
    [
        ({"voices": "af_bella"}, "voices"),
        ({"voices": [["af_bella", 0]]}, "voices"),
        ({"pitch_semitones": 7}, "pitch_semitones"),
        ({"speed": 2.0}, "speed"),
        ({"fallback": ["edge", "banana"]}, "fallback"),
        ({"engine": "banana"}, "engine"),
    ],
)
def test_bad_tts_values_fall_back_to_defaults(tmp_path, tts_patch, bad_key):
    data = json.loads(json.dumps(config_mod.DEFAULT_CONFIG))
    data["tts"].update(tts_patch)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = config_mod.load_config(path)
    assert any(p.startswith(f"tts.{bad_key}") for p in cfg.problems)
    assert cfg.tts[bad_key] == config_mod.DEFAULT_CONFIG["tts"][bad_key]


def test_save_voice_choice_keeps_other_settings(tmp_path):
    path = tmp_path / "config.json"
    data = json.loads(json.dumps(config_mod.DEFAULT_CONFIG))
    data["printer"] = "My Printer"
    path.write_text(json.dumps(data), encoding="utf-8")
    config_mod.save_voice_choice([["af_sky", 0.6], ["af_nova", 0.4]], 3, 1.25, path)
    cfg = config_mod.load_config(path)
    assert cfg.printer == "My Printer"
    assert cfg.tts["voices"] == [["af_sky", 0.6], ["af_nova", 0.4]]
    assert cfg.tts["pitch_semitones"] == 3 and cfg.tts["speed"] == 1.25
    config_mod.save_voice_choice([["af_sky", 1]], 9, 5, path)  # clamped to the allowed range
    cfg = config_mod.load_config(path)
    assert cfg.tts["pitch_semitones"] == 4.0 and cfg.tts["speed"] == 1.3 and not cfg.problems


# ------------------------------------------------------------ speaker fallback chain

class _Fake(tts.TTSEngine):
    calls: list[str] = []
    fail = False
    ready = True

    def is_ready(self):
        return self.ready

    def synthesize(self, text, out_path):
        type(self).calls.append(text)
        if self.fail:
            raise RuntimeError(f"{self.name} broken")
        Path(out_path).write_bytes(b"RIFF" + b"\0" * 200)


def _engines(monkeypatch, **flags):
    classes = {}
    for name in ("kokoro", "edge", "sapi"):
        cls = type(name.title(), (_Fake,), {"name": name, "calls": [], **flags.get(name, {})})
        classes[name] = cls
    monkeypatch.setattr(tts, "ENGINES", classes)
    return classes


def test_speaker_uses_primary_and_caches(monkeypatch):
    e = _engines(monkeypatch)
    sp = tts.Speaker({"engine": "kokoro", "fallback": ["edge", "sapi"]})
    p1 = sp.audio_for("Hi hi!")
    p2 = sp.audio_for("Hi hi!")
    assert p1 == p2 and p1.name.startswith("kokoro_")
    assert e["kokoro"].calls == ["Hi hi!"]


def test_speaker_falls_back_in_order(monkeypatch):
    e = _engines(monkeypatch, kokoro={"fail": True}, edge={"fail": True})
    sp = tts.Speaker({"engine": "kokoro", "fallback": ["edge", "sapi"]})
    assert sp.audio_for("Printing now~!").name.startswith("sapi_")
    assert e["kokoro"].calls == ["Printing now!"] and e["edge"].calls == ["Printing now!"]
    # a broken engine is skipped for a while instead of being retried every line
    sp.audio_for("All done!")
    assert e["kokoro"].calls == ["Printing now!"]


def test_speaker_skips_primary_while_downloading(monkeypatch):
    e = _engines(monkeypatch, kokoro={"ready": False})
    sp = tts.Speaker({"engine": "kokoro", "fallback": ["edge", "sapi"]})
    assert sp.audio_for("Hi!").name.startswith("edge_")
    assert e["kokoro"].calls == []


def test_speaker_prepare_prerenders(monkeypatch):
    e = _engines(monkeypatch)
    statuses = []
    sp = tts.Speaker({"engine": "kokoro", "fallback": ["edge"]}, on_status=statuses.append)
    sp._prepare(["All done!", "Printing now~!", "All done!"])
    assert e["kokoro"].calls == ["All done!", "Printing now!"]
    assert statuses[-1] is None and "Preparing voice lines 1/2" in statuses
    assert sp.is_cached("Printing now~!")


def test_streamed_say_plays_chunks_in_order(monkeypatch):
    _engines(monkeypatch)
    played = []
    monkeypatch.setattr(tts, "play_file", lambda p, should_stop=None: played.append(p))
    sp = tts.Speaker({"engine": "kokoro", "fallback": []})
    sp.say("Print document1.pdf from Juan Dela Cruz, 3 pages, on EPSON L120? Yes or no?")
    assert len(played) >= 3


def test_say_stops_when_cancelled(monkeypatch):
    _engines(monkeypatch)
    played = []
    cancel = threading.Event()

    def fake_play(p, should_stop=None):
        played.append(p)
        cancel.set()

    monkeypatch.setattr(tts, "play_file", fake_play)
    sp = tts.Speaker({"engine": "kokoro", "fallback": []})
    sp.say("Print document1.pdf from Juan Dela Cruz, 3 pages, on EPSON L120? Yes or no?", cancel_event=cancel)
    assert len(played) == 1


def test_own_name_echo_guard(monkeypatch):
    _engines(monkeypatch)
    monkeypatch.setattr(tts, "play_file", lambda p, should_stop=None: None)
    sp = tts.Speaker({"engine": "kokoro", "fallback": []})
    sp.say("All done!")
    assert not sp.said_own_name_recently()
    sp.say("I'm Rika, your printing buddy!")
    assert sp.said_own_name_recently()           # just said it -> ignore "Rika" from the speakers
    sp.name_said_at -= 5
    assert not sp.said_own_name_recently()       # a few seconds later her name works again


def test_main_voice_beats_cached_fallback_clip(monkeypatch):
    """A clip cached earlier by a fallback voice must not be played while the main voice works."""
    e = _engines(monkeypatch, kokoro={"fail": True})
    sp = tts.Speaker({"engine": "kokoro", "fallback": ["edge"]})
    assert sp.audio_for("Hi hi!").name.startswith("edge_")   # main voice down -> fallback clip cached
    e["kokoro"].fail = False
    sp._down_until.clear()                                    # main voice is back
    assert sp.audio_for("Hi hi!").name.startswith("kokoro_")
