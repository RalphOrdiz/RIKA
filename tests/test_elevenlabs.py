"""ElevenLabs engine against a fake API (no network, no credits)."""
import io
import json
import logging
import urllib.error

import pytest

from rika import config as config_mod
from rika import tts

VOICE = "ZvyLfzObBII0WC7C5sBp"
SECRET = "sk_test_do_not_leak_123"


class FakeAPI:
    def __init__(self, status=200, audio=b"ID3" + b"\0" * 2000):
        self.status, self.audio, self.requests = status, audio, []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if self.status != 200:
            body = json.dumps({"detail": {"message": "nope"}}).encode()
            raise urllib.error.HTTPError(req.full_url, self.status, "err", {}, io.BytesIO(body))
        payload = json.dumps({"name": "Rika"}).encode() if req.get_method() == "GET" else self.audio
        return io.BytesIO(payload)


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI()
    monkeypatch.setattr(tts.urllib.request, "urlopen", fake)
    monkeypatch.setenv("ELEVENLABS_API_KEY", SECRET)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    return fake


def engine(**opts):
    return tts.ElevenLabsEngine({"elevenlabs": {"voice_id": VOICE, "model_id": "eleven_multilingual_v2", **opts}})


def test_synthesize_uses_voice_model_and_key_header(api, tmp_path):
    e = engine()
    e.prepare()
    out = tmp_path / "a.mp3"
    e.synthesize("Printing now~!", out)
    post = api.requests[-1]
    assert post.get_method() == "POST"
    assert post.full_url.startswith(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}")
    body = json.loads(post.data)
    assert body == {"text": "Printing now!", "model_id": "eleven_multilingual_v2"}
    assert post.get_header("Xi-api-key") == SECRET  # only ever in the header
    assert out.read_bytes() == api.audio


def test_env_voice_id_overrides_config(api, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "OTHERVOICE")
    assert engine().voice_id == "OTHERVOICE"


def test_rejected_key_is_reported_without_leaking_it(api, tmp_path, caplog):
    api.status = 401
    e = engine()
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError) as err:
        e.prepare()
    assert "rejected" in str(err.value)
    assert SECRET not in str(err.value) and SECRET not in caplog.text
    assert not e.is_ready()  # Speaker will use the fallback voices


def test_unknown_voice(api):
    api.status = 404
    with pytest.raises(RuntimeError, match="not found"):
        engine().prepare()


def test_missing_key_means_not_ready(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    e = engine()
    assert not e.is_ready()
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        e.prepare()


def test_speaker_falls_back_to_next_engine_without_key(monkeypatch, tmp_path):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    class FakeKokoro(tts.TTSEngine):
        name = "kokoro"

        def synthesize(self, text, out_path):
            out_path.write_bytes(b"RIFF" + b"\0" * 200)

    monkeypatch.setitem(tts.ENGINES, "kokoro", FakeKokoro)
    sp = tts.Speaker({"engine": "elevenlabs", "fallback": ["kokoro"], "elevenlabs": {"voice_id": VOICE}})
    assert sp.audio_for("All done!").name.startswith("kokoro_")


def test_env_file_is_loaded_but_real_env_wins(tmp_path, monkeypatch, isolated_appdata):
    isolated_appdata.mkdir(parents=True, exist_ok=True)
    (isolated_appdata / ".env").write_text(
        "# comment\nELEVENLABS_API_KEY=from_file\nexport ELEVENLABS_VOICE_ID='quoted'\nEMPTY=\n", encoding="utf-8"
    )
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "real_env")
    config_mod.load_env_files()
    import os

    assert os.environ["ELEVENLABS_API_KEY"] == "from_file"
    assert os.environ["ELEVENLABS_VOICE_ID"] == "real_env"
    assert "EMPTY" not in os.environ
    monkeypatch.delenv("ELEVENLABS_API_KEY")


def test_config_accepts_elevenlabs_engine(tmp_path):
    data = json.loads(json.dumps(config_mod.DEFAULT_CONFIG))
    data["tts"]["engine"] = "elevenlabs"
    data["tts"]["fallback"] = ["kokoro", "edge", "sapi"]
    data["tts"]["elevenlabs"]["voice_id"] = VOICE
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = config_mod.load_config(path)
    assert cfg.problems == [] and cfg.tts["elevenlabs"]["model_id"] == "eleven_multilingual_v2"
