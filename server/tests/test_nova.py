import json
import subprocess
import time

import httpx
import pytest

from nova import config, db, tools
from nova.agent import Agent


@pytest.fixture(autouse=True)
def tmp_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "j.sqlite3")
    proj = tmp_path / "projects"
    (proj / "demo").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=proj / "demo", check=True)
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty",
                    "-m", "ilk"], cwd=proj / "demo", check=True)
    monkeypatch.setattr(config, "PROJECTS_DIR", proj)
    monkeypatch.setattr(config, "HOME_DIR", tmp_path)
    return proj


def fake_ollama(script):
    """script: sırayla dönecek model mesajları. stream isteğinde cevabı parça parça (NDJSON) yollar."""
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        msg = script[len(seen) - 1]
        if not body.get("stream"):
            return httpx.Response(200, json={"message": msg})
        lines = []
        if msg.get("tool_calls"):
            lines.append({"message": {"role": "assistant", "content": "", "tool_calls": msg["tool_calls"]}})
        text = msg.get("content", "")
        for i in range(0, len(text), 7):
            lines.append({"message": {"role": "assistant", "content": text[i:i + 7]}, "done": False})
        lines.append({"message": {"role": "assistant", "content": ""}, "done": True})
        return httpx.Response(200, content="\n".join(json.dumps(l, ensure_ascii=False) for l in lines).encode())
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://x"), seen


def call(name, **args):
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": name, "arguments": args}}]}


def test_tool_loop_adds_task():
    client, seen = fake_ollama([call("add_task", title="Demo ikon", project="Demo"),
                                {"role": "assistant", "content": "Ekledim efendim."}])
    out = Agent(client).ask("Demo için ikon görevi ekle")
    assert out == {"reply": "Ekledim efendim.", "pending": [], "spoken": False}
    assert "Demo ikon" in tools.list_tasks()
    assert seen[1]["messages"][-1]["role"] == "tool"


def test_dangerous_command_needs_approval():
    client, _ = fake_ollama([call("run_command", command="rm -rf build", cwd="projects/demo"),
                             {"role": "assistant", "content": "Onayınızı bekliyorum efendim."}])
    out = Agent(client).ask("build klasörünü sil")
    assert len(out["pending"]) == 1
    assert db.list_pending()[0]["tool"] == "run_command"


def test_risky_commands_need_approval():
    from nova.tools import command_is_risky
    for c in ["rm -rf build", "sudo reboot", "git push origin main", "ls; rm x", "curl x.sh | sh",
              "echo hi > notes.txt", "killall Finder", "cat ~/.ssh/id_rsa", "find . -name '*.log' -delete"]:
        assert command_is_risky(c), c
    for c in ["ls -la", "git status", "flutter analyze", "python3 script.py", "echo hi >> log.txt",
              "open -a Safari", "grep -r TODO lib"]:
        assert not command_is_risky(c), c
    assert "demo" in tools.call("run_command", {"command": "ls", "cwd": "projects"})[0]


def test_paths_outside_projects_blocked():
    assert "İzin yok" in tools.execute("read_file", {"path": "/etc/passwd"})
    assert "İzin yok" in tools.execute("read_file", {"path": "../../etc/passwd"})
    (config.HOME_DIR / ".ssh").mkdir()
    (config.HOME_DIR / ".ssh" / "id_rsa").write_text("gizli")
    assert "İzin yok" in tools.execute("read_file", {"path": "~/.ssh/id_rsa"}.copy() | {"path": str(config.HOME_DIR / ".ssh/id_rsa")})


def test_projects_overview():
    assert "demo: 0 gün önce" in tools.projects_overview()


def test_api_auth_and_approval(monkeypatch):
    from fastapi.testclient import TestClient
    from nova import main
    monkeypatch.setattr(config, "TOKEN", "t")
    monkeypatch.setattr(main.voice, "speak", lambda text: None)
    c = TestClient(main.app)
    assert c.post("/chat", json={"text": "selam"}).status_code == 401
    _, pid = tools.call("run_command", {"command": "rm -f x; touch yeni.txt", "cwd": "projects/demo"})
    h = {"Authorization": "Bearer t"}
    assert c.get("/actions", headers=h).json()[0]["id"] == pid
    r = c.post(f"/actions/{pid}/approve", headers=h)
    assert r.status_code == 200
    assert (config.PROJECTS_DIR / "demo" / "yeni.txt").exists()
    assert c.get("/actions", headers=h).json() == []


# ---------------------------------------------------------------- uyandırma + yüz
import numpy as np

from nova import face
from nova.wake import Segmenter, find_wake, is_goodbye


def test_find_wake():
    assert find_wake("Nova, projelerim ne durumda?") == (True, "projelerim ne durumda")
    assert find_wake("nova") == (True, "")
    assert find_wake("yeni bir şey yaptım")[0] is False
    assert find_wake("novalar")[0] is False


def test_goodbye():
    assert is_goodbye("Teşekkürler Nova")
    assert not is_goodbye("teşekkür mailine cevap yazar mısın ve projeleri listeler misin")


def test_segmenter_cuts_speech():
    seg = Segmenter(threshold=.02, end_silence=.5, max_len=10)
    quiet, loud = np.zeros(1600, np.float32), np.full(1600, .1, np.float32)
    out = None
    for b in [quiet] * 5 + [loud] * 8 + [quiet] * 6:
        out = seg.feed(b) if out is None else out
    assert out is not None
    assert 12 * 1600 <= len(out) <= 18 * 1600       # ön kayıt + konuşma + bitiş sessizliği


def test_face_page_and_query_token(monkeypatch):
    from fastapi.testclient import TestClient
    from nova import main
    monkeypatch.setattr(config, "TOKEN", "t")
    c = TestClient(main.app)
    assert c.get("/face").status_code == 401
    r = c.get("/face?t=t")
    assert r.status_code == 200 and "NOVA" in r.text
    face.bus.ended.clear()
    assert c.post("/face/ended?t=t").status_code == 200
    assert face.bus.ended.is_set()


def test_bus_publish():
    q = face.bus.subscribe()
    face.bus.state("thinking")
    assert q.get_nowait() == {"type": "state", "state": "thinking"}
    face.bus.unsubscribe(q)


class NoBargeIn:
    def __init__(self, *a): pass
    def start(self): pass
    def stop(self): pass


def test_wake_conversation_flow(monkeypatch):
    """İsim → komut → cevap → takip sorusu → vedalaşma → yüz kapanır."""
    from nova import wake
    heard = iter(["Nova", "projelerim ne durumda", "teşekkürler"])
    events, said = [], []

    class FakeMic:
        def flush(self): pass
        def segment(self, **kw): return np.ones(16000, np.float32)

    class FakeAgent:
        def ask(self, text, **kw): return {"reply": f"cevap: {text}", "pending": []}

    monkeypatch.setattr(wake.voice, "transcribe", lambda *a, **k: next(heard))
    monkeypatch.setattr(wake.voice, "speak", lambda text, *a: None)
    monkeypatch.setattr(wake.voice, "chime", lambda: events.append("chime"))
    monkeypatch.setattr(wake, "open_face", lambda: events.append("open"))
    monkeypatch.setattr(wake, "close_face", lambda: events.append("close"))
    monkeypatch.setattr(wake.bus, "speak", lambda audio, text, **k: said.append(text))
    monkeypatch.setattr(wake.time, "sleep", lambda s: None)
    monkeypatch.setattr(wake, "BargeIn", NoBargeIn)
    wake.WakeListener(FakeAgent())._wait_for_name(FakeMic())
    assert events == ["open", "chime", "close"]
    assert said == ["cevap: projelerim ne durumda", "Rica ederim efendim."]


def test_speak_falls_back_when_engines_fail(monkeypatch):
    from nova import voice
    calls = []
    def fail(label):
        def f(t, p, lang="tr"):
            calls.append(label)
            raise RuntimeError("yok")
        return f
    monkeypatch.setattr(voice, "ENGINES", {k: (fail(k), ext) for k, (_, ext) in voice.ENGINES.items()})
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    assert voice.speak("merhaba") is None
    assert calls == ["edge", "piper", "say"]


def test_brain_errors_are_explained():
    def client(status, body):
        return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body)),
                            base_url="http://x")
    out = Agent(client(404, '{"error":"model \\"qwen3:8b\\" not found, try pulling it first"}')).ask("selam")
    assert "ollama pull" in out["reply"]
    def refused(r): raise httpx.ConnectError("refused")
    out = Agent(httpx.Client(transport=httpx.MockTransport(refused), base_url="http://x")).ask("selam")
    assert "Ollama kapalı" in out["reply"]


def _fake_weather_http(monkeypatch):
    def handler(req):
        if "geocoding" in req.url.host:
            return httpx.Response(200, json={"results": [{"name": "İstanbul", "country": "Türkiye",
                                                          "latitude": 59.33, "longitude": 18.07}]})
        return httpx.Response(200, json={
            "current": {"time": "2026-11-09T16:15", "temperature_2m": 3.4, "apparent_temperature": -0.6,
                        "weather_code": 3, "wind_speed_10m": 5.2, "precipitation": 0},
            "daily": {"time": ["2026-11-09", "2026-11-10"], "temperature_2m_max": [4.1, 5.0],
                      "temperature_2m_min": [1.2, 2.0], "weather_code": [3, 61],
                      "precipitation_probability_max": [10, 70]}})
    monkeypatch.setattr(tools, "_http", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_weather_tool_uses_real_numbers(monkeypatch):
    _fake_weather_http(monkeypatch)
    out = tools.get_weather()
    assert "İstanbul" in out and "3°C" in out and "kapalı" in out and "hissedilen -1°C" in out


def test_made_up_weather_is_rejected_and_tool_forced(monkeypatch):
    _fake_weather_http(monkeypatch)
    client, seen = fake_ollama([
        {"role": "assistant", "content": "İstanbul'da hava 22 derece ve güneşli efendim."},   # uydurma
        call("get_weather", city="İstanbul"),
        {"role": "assistant", "content": "İstanbul'da şu an 3 derece, hava kapalı efendim."}])
    out = Agent(client).ask("Bugün İstanbul'da hava nasıl?")
    assert out["reply"] == "İstanbul'da şu an 3 derece, hava kapalı efendim."
    assert "sistem notu" in seen[1]["messages"][-1]["content"]
    assert "3°C" in seen[2]["messages"][-1]["content"]           # model gerçek veriyi gördü


def test_old_history_is_not_reused(monkeypatch):
    db.add_message("assistant", "İstanbul'da 22 derece güneşli.")
    db.execute("UPDATE messages SET created = created - 7200")
    assert db.recent_messages(12, 30) == []


def test_approval_words():
    from nova.wake import approval_decision
    assert approval_decision("Onaylıyorum") is True
    assert approval_decision("evet yap") is True
    assert approval_decision("hayır iptal et") is False
    assert approval_decision("yapma") is False
    assert approval_decision("YouTube stüdyosundaki videoların listesini oku bakalım lütfen hemen şimdi") is None


def test_voice_approval_runs_pending_action(monkeypatch):
    from nova import wake
    _, pid = tools.call("run_command", {"command": "rm -f x; touch onaylandi.txt", "cwd": "projects/demo"})
    heard = iter(["Nova", "build al", "onaylıyorum", "teşekkürler"])
    said = []

    class FakeMic:
        def flush(self): pass
        def segment(self, **kw): return np.ones(16000, np.float32)

    class FakeAgent:
        def ask(self, text, **kw): return {"reply": "Komutu çalıştırmam için onayınız gerekiyor.", "pending": [pid]}

    monkeypatch.setattr(wake.voice, "transcribe", lambda *a, **k: next(heard))
    monkeypatch.setattr(wake.voice, "speak", lambda text, *a: None)
    monkeypatch.setattr(wake.voice, "chime", lambda: None)
    monkeypatch.setattr(wake, "open_face", lambda: None)
    monkeypatch.setattr(wake, "close_face", lambda: None)
    monkeypatch.setattr(wake.bus, "speak", lambda audio, text, **k: said.append(text))
    monkeypatch.setattr(wake.time, "sleep", lambda s: None)
    monkeypatch.setattr(wake, "BargeIn", NoBargeIn)
    wake.WakeListener(FakeAgent())._wait_for_name(FakeMic())
    assert said[:2] == ["Komutu çalıştırmam için onayınız gerekiyor.", "Onaylıyor musunuz efendim?"]
    assert said[2].startswith("Efendim, tamamlandı")
    assert (config.PROJECTS_DIR / "demo" / "onaylandi.txt").exists()


def test_reply_cleanup_and_fake_action_guard():
    from nova.agent import _clean_reply, _unverified_claim
    assert _clean_reply("Tamam efendim! 🚀🎬 **Hazır**") == "Tamam efendim! Hazır"
    assert _unverified_claim("Titanic videosunu onayladım.", set()) == "ilgili işlem"


def test_saved_instruction_goes_into_system_prompt():
    from nova.agent import system_prompt
    tools.execute("save_instruction", {"text": "Cevaplarda emoji kullanma"})
    assert "Cevaplarda emoji kullanma" in system_prompt()


def test_streaming_speaks_sentence_by_sentence(monkeypatch):
    _fake_weather_http(monkeypatch)
    client, _ = fake_ollama([call("get_weather"),
                             {"role": "assistant", "content": "Şu an 3 derece ve kapalı. Yarın 2.5 derece, çisenti var! Mont al."}])
    said = []
    out = Agent(client).ask("hava nasıl", on_sentence=said.append)
    assert said == ["Şu an 3 derece ve kapalı.", "Yarın 2.5 derece, çisenti var!", "Mont al."]
    assert out["spoken"] is True


def test_streaming_guard_stops_made_up_weather(monkeypatch):
    _fake_weather_http(monkeypatch)
    client, _ = fake_ollama([{"role": "assistant", "content": "İstanbul'da 22 derece güneşli. Keyfini çıkar."},
                             call("get_weather"),
                             {"role": "assistant", "content": "Şu an 3 derece, kapalı."}])
    said = []
    Agent(client).ask("hava nasıl", on_sentence=said.append)
    assert said == ["Şu an 3 derece, kapalı."]


def test_memory_goes_into_prompt():
    from nova.agent import system_prompt
    tools.execute("remember_fact", {"fact": "Kahveyi sütsüz içer"})
    assert "Kahveyi sütsüz içer" in system_prompt()
    assert "1 kayıt silindi" in tools.execute("forget_fact", {"query": "kahve"})
    assert "kahve" not in system_prompt()


def test_mac_tool_helpers(monkeypatch):
    from nova import mac
    scripts = []
    monkeypatch.setattr(mac, "osa", lambda s, timeout=30: scripts.append(s) or "tamam")
    assert mac.press_keys("cmd+s") == "cmd+s basıldı."
    assert scripts[-1].endswith('keystroke "s" using {command down}')
    mac.press_keys("enter")
    assert scripts[-1].endswith("key code 36")
    assert mac._q('a "b"') == '"a \\"b\\""'
    assert tools.TOOLS["click_on_screen"].confirm({"text": "Gönder"})
    assert not tools.TOOLS["click_on_screen"].confirm({"text": "Kaydet"})
    assert tools.TOOLS["trash_file"].confirm({"path": "x"})
    (config.HOME_DIR / "var.txt").write_text("eski")
    assert tools.TOOLS["write_file"].confirm({"path": "var.txt", "content": "y"})
    assert not tools.TOOLS["write_file"].confirm({"path": "yeni.txt", "content": "y"})
    assert "Yazıldı" in tools.execute("write_file", {"path": "yeni.txt", "content": "selam"})


def test_repeated_answer_is_rejected():
    db.add_message("user", "o 7 kullanıcı kim")
    db.add_message("assistant", "Şu an 7 aktif kullanıcı var ama bu bilgiyi tam olarak veremiyorum efendim.")
    client, seen = fake_ollama([
        {"role": "assistant", "content": "Şu an 7 aktif kullanıcı var ama bu bilgiyi tam olarak veremiyorum efendim."},
        {"role": "assistant", "content": "Haklısın, o sayı aslında açık terminal pencereleri, kullanıcı değil."}])
    out = Agent(client).ask("bunu az önce söyledin, niye tekrar ediyorsun?")
    assert out["reply"].startswith("Haklısın")
    assert "tekrar ettin" in seen[1]["messages"][-1]["content"]


def test_cleanup_removes_parroted_time_and_boilerplate():
    from nova.agent import _clean_reply
    out = _clean_reply("Efendim, kaldırdım. Dilerseniz, başka bir şey de yapabilirim! "
                       "09.11.2026 16:49'da kontrol ediyorum, her şey yolunda!")
    assert out == "Efendim, kaldırdım."
    assert _clean_reply("Efendim, özür ederim.") == "Efendim, özür dilerim."


def test_no_timestamp_in_user_message():
    client, seen = fake_ollama([{"role": "assistant", "content": "Merhaba!"}])
    Agent(client).ask("merhaba")
    assert seen[0]["messages"][-1]["content"] == "merhaba"


def test_goodbye_variants():
    from nova.wake import is_goodbye
    assert is_goodbye("Şu an hiçbir şey yapma, kapat.")
    assert is_goodbye("Tamam çıkış yap")
    assert not is_goodbye("ekranı kapatma ayarını değiştir ve sonra bana raporla lütfen hemen şimdi tamam")


def test_system_status_has_no_user_count(monkeypatch):
    outs = {"uptime": "17:15  up 10 days,  4:52, 7 users, load averages: 2.1 2.0 1.9",
            "pmset": "Now drawing from 'AC Power'\n -InternalBattery-0 (id=1)\t80%; charged; 0:00 remaining",
            "df": "Filesystem Size Used Avail Capacity\n/dev/disk3s1 460Gi 210Gi 230Gi 48% /"}
    monkeypatch.setattr(tools, "_run", lambda cmd, *a, **k: outs[cmd[0]])
    out = tools.system_status()
    assert "10 days,  4:52" in out and "users" not in out and "%80" in out and "230Gi boş" in out


# ---------------------------------------------------------------- Claude beyni
from types import SimpleNamespace as NS


class FakeAnthropic:
    """Sırayla cevap dönen sahte Anthropic istemcisi; gönderilen istekleri saklar."""
    def __init__(self, script):
        self.script, self.sent = script, []
        self.messages = NS(stream=self._stream)

    def _stream(self, **kw):
        self.sent.append(kw)
        blocks = self.script[len(self.sent) - 1]
        text = "".join(b.text for b in blocks if b.type == "text")

        class S:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            text_stream = [text[i:i + 9] for i in range(0, len(text), 9)]
            def get_final_message(s):
                return NS(content=blocks, usage=NS(input_tokens=100, cache_read_input_tokens=90, output_tokens=20))
        return S()


def T(text): return NS(type="text", text=text)
def U(id, name, **inp): return NS(type="tool_use", id=id, name=name, input=inp)


def _claude_agent(script):
    from nova.claude_brain import ClaudeBrain
    a = Agent(httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), base_url="http://x"))
    brain = ClaudeBrain.__new__(ClaudeBrain)
    brain.client = FakeAnthropic(script)
    a.claude = brain
    return a, brain.client


def test_claude_converts_and_uses_tools(monkeypatch):
    _fake_weather_http(monkeypatch)
    a, fake = _claude_agent([[T("Bakıyorum."), U("tu1", "get_weather", city="İstanbul")],
                             [T("İstanbul'da şu an 3 derece ve kapalı, mont al.")]])
    said = []
    out = a.ask("hava nasıl", on_sentence=said.append)
    assert out["reply"] == "İstanbul'da şu an 3 derece ve kapalı, mont al."
    assert said == ["Bakıyorum.", "İstanbul'da şu an 3 derece ve kapalı, mont al."]
    second = fake.sent[1]
    assert second["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert second["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    roles = [m["role"] for m in second["messages"]]
    assert roles == ["user", "assistant", "user"]                      # sıralı roller
    assert second["messages"][1]["content"][-1] == {"type": "tool_use", "id": "tu1", "name": "get_weather",
                                                     "input": {"city": "İstanbul"}}
    res = second["messages"][2]["content"][0]
    assert res["type"] == "tool_result" and res["tool_use_id"] == "tu1" and "3°C" in res["content"]


def test_claude_failure_falls_back_to_local():
    from nova.claude_brain import ClaudeBrain
    client, _ = fake_ollama([{"role": "assistant", "content": "Lokal modelden merhaba."}])
    a = Agent(client)
    brain = ClaudeBrain.__new__(ClaudeBrain)
    def boom(**kw): raise RuntimeError("bağlantı yok")
    brain.client = NS(messages=NS(stream=boom))
    a.claude = brain
    assert a.ask("merhaba")["reply"] == "Lokal modelden merhaba."


def test_each_language_gets_native_voice(monkeypatch):
    from nova import voice
    used = []
    monkeypatch.setattr(voice, "ENGINES", {"edge": (lambda t, p, lang="tr": used.append(lang) or (_ for _ in ()).throw(RuntimeError()), ".mp3"),
                                           "piper": (lambda t, p, lang="tr": (_ for _ in ()).throw(RuntimeError()), ".wav"),
                                           "say": (lambda t, p, lang="tr": (_ for _ in ()).throw(RuntimeError()), ".aiff")})
    monkeypatch.setattr(config, "TTS_ENGINE", "edge")
    voice.speak("No aplicativo, os usuários compartilham todos os dias um vídeo curto.")
    voice.speak("Ett ögonblick går inte till alla, det går till en enda person.")
    voice.speak("Uygulamada kullanıcılar her gün kısa bir video paylaşıyor.")
    assert used == ["pt", "sv", "tr"]
    assert voice.detect_lang("Tamam.", default="sv") == "sv"          # kısa cümle önceki dilde kalır
    assert voice.EDGE_VOICES["pt"].startswith("pt-") and voice.EDGE_VOICES["sv"].startswith("sv-")


def test_barge_in_stops_speech(monkeypatch):
    from nova import wake
    events, played = [], []
    monkeypatch.setattr(wake.voice, "speak", lambda text, *a: "x.m4a")
    monkeypatch.setattr(wake.voice, "transcribe", lambda *a, **k: "Dur Nova dur!")

    def slow_speak(audio, text, **k):
        played.append(text)
        wake.bus.ended.clear()
        wake.bus.ended.wait(5)                 # gerçek çalma gibi: ended gelene kadar bekle
    monkeypatch.setattr(wake.bus, "speak", slow_speak)
    monkeypatch.setattr(wake.bus, "publish", lambda **e: events.append(e))

    class FakeMic:
        def flush(self): pass
        def segment(self, **kw):
            time.sleep(0.2)
            return np.ones(8000, np.float32)

    sp = wake.Speaker()
    barge = wake.BargeIn(FakeMic(), sp)
    for i in range(5):
        sp.say(f"Bu uzun cevabın {i + 1}. cümlesi.")
    barge.start()
    sp.finish(10)
    barge.stop()
    assert sp.interrupted and len(played) < 5
    assert {"type": "stop"} in events
    assert sp.say("devam") is False            # model de durdurulur


def test_own_voice_does_not_interrupt(monkeypatch):
    from nova import wake
    monkeypatch.setattr(wake.voice, "transcribe", lambda *a, **k: "biraz bekle")
    sp = wake.Speaker.__new__(wake.Speaker)
    sp.interrupted, sp.current, sp.recent = False, "Lütfen biraz bekle, hemen bakıyorum.", []
    hits = []
    sp.interrupt = lambda: hits.append(1)

    class OneShotMic:
        n = 0
        def flush(self): pass
        def segment(self, **kw):
            self.n += 1
            if self.n > 2:
                kw["stop"].set()
                return None
            return np.ones(8000, np.float32)
    b = wake.BargeIn(OneShotMic(), sp)
    b.run()
    assert hits == []                          # "bekle" Nova'nın kendi cümlesinde geçiyordu


def test_interrupt_stops_generation():
    client, _ = fake_ollama([{"role": "assistant", "content": "Birinci cümle burada. İkinci cümle. Üçüncü cümle."}])
    said = []
    def on_sentence(t):
        if said:
            return False
        said.append(t)
    out = Agent(client).ask("anlat", on_sentence=on_sentence)
    assert said == ["Birinci cümle burada."]


def test_guards_off_for_claude(monkeypatch):
    """Claude saati kendisi bilse bile geri çevrilmez; 'aracı çağırmamıştım' itiraflarına yol açmaz."""
    a, fake = _claude_agent([[T("Saat 00:54 efendim, gecenin biri.")]])
    said = []
    out = a.ask("saat kaç", on_sentence=said.append)
    assert len(fake.sent) == 1 and said == ["Saat 00:54 efendim, gecenin biri."]


def test_meta_tool_talk_is_removed():
    from nova.agent import _clean_reply
    out = _clean_reply("Baktım, saat 00:55. Az önce söylediğimde aracı çağırmamıştım, neyse ki tutmuş. Ayrı odada beklemek iyi fikir.")
    assert "aracı" not in out and "Ayrı odada beklemek iyi fikir." in out


# ---------------------------------------------------------------- ev: Apple TV
class _FakeTV:
    """pyatv bağlantısının taklidi: ne çağrıldığını `log` listesine yazar."""
    def __init__(self, on=False, apps=(("Spotify", "com.spotify.client"), ("Müzik", "com.apple.TVMusic"),
                                       ("YouTube", "com.google.ios.youtube"), ("Netflix", "com.netflix.Netflix"))):
        from types import SimpleNamespace as NS
        from pyatv.const import DeviceState, PowerState
        tv, self.log = self, []
        self.playing = NS(device_state=DeviceState.Idle, title=None, artist=None)

        class Power:
            power_state = PowerState.On if on else PowerState.Off
            async def turn_on(self, await_new_state=False):
                if await_new_state:                 # gerçek Apple TV (Companion) böyle davranır
                    raise NotImplementedError("not supported by Companion yet")
                tv.log.append("on"); self.power_state = PowerState.On
            async def turn_off(self, await_new_state=False):
                if await_new_state:
                    raise NotImplementedError("not supported by Companion yet")
                tv.log.append("off"); self.power_state = PowerState.Off

        class Apps:
            async def app_list(self):
                return [NS(name=n, identifier=i) for n, i in apps]
            async def launch_app(self, ident):
                tv.log.append("launch:" + ident)

        class Keys:
            def __getattr__(self, name):
                async def press():
                    tv.log.append(name)
                return press

        class Meta:
            app = NS(name="Spotify", identifier="com.spotify.client")
            async def playing(self):
                return tv.playing

        self.power, self.apps, self.remote_control, self.audio, self.metadata = Power(), Apps(), Keys(), Keys(), Meta()

    def close(self):
        self.log.append("close")
        return set()


@pytest.fixture
def tv(monkeypatch):
    import asyncio
    from nova import home
    fake = _FakeTV()

    async def connect():
        return fake

    async def no_sleep(_):
        return None
    monkeypatch.setattr(home, "_connect", connect)
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    return fake


def test_tv_power_on_off(tv):
    assert tools.call("tv_power", {"state": "on"}) == ("Televizyon açıldı.", None)
    assert tools.call("tv_power", {"state": "on"})[0] == "Televizyon zaten açık."
    assert "kapatıldı" in tools.call("tv_power", {"state": "off"})[0]
    assert tv.log == ["on", "close", "close", "off", "close"]       # onay istemez, her seferinde bağlantı kapanır


def test_tv_open_app_wakes_tv_and_matches_name(tv):
    assert tools.execute("tv_open_app", {"name": "you tube"}) == "Televizyonda YouTube açıldı."
    assert tv.log[:2] == ["on", "launch:com.google.ios.youtube"]
    assert tools.execute("tv_open_app", {"name": "apple music"}) == "Televizyonda Müzik açıldı."
    out = tools.execute("tv_open_app", {"name": "HBO"})
    assert "yok" in out and "Netflix" in out and tv.log.count("launch:com.apple.TVMusic") == 1


def test_tv_play_music_prefers_spotify_then_configured_app(tv, monkeypatch):
    assert "Spotify" in tools.execute("tv_play_music", {})
    assert tv.log == ["on", "launch:com.spotify.client", "play", "close"]
    monkeypatch.setattr(config, "TV_MUSIC_APP", "Müzik")
    tools.execute("tv_play_music", {})
    assert "launch:com.apple.TVMusic" in tv.log


def test_tv_remote_and_status(tv):
    from pyatv.const import DeviceState
    assert tools.execute("tv_remote", {"command": "volume_up", "times": 3}).endswith("x3 gönderildi.")
    assert tv.log[:3] == ["volume_up"] * 3
    assert "Bilinmeyen" in tools.execute("tv_remote", {"command": "self_destruct"})
    assert "kapalı" in tools.execute("tv_status", {})
    tools.execute("tv_power", {"state": "on"})
    tv.playing.device_state, tv.playing.title, tv.playing.artist = DeviceState.Playing, "Gülümse", "Sezen Aksu"
    out = tools.execute("tv_status", {})
    assert "Sezen Aksu - Gülümse (çalıyor)" in out and "Spotify" in out


def test_tv_errors_are_spoken_not_raised(monkeypatch):
    from nova import home

    async def unpaired():
        raise home.TVError(home.PAIR_HELP)
    monkeypatch.setattr(home, "_connect", unpaired)
    assert "atvremote wizard" in tools.execute("tv_power", {"state": "on"})

    async def boom():
        raise OSError("ağ yok")
    monkeypatch.setattr(home, "_connect", boom)
    assert tools.execute("tv_status", {}).startswith("Apple TV hatası: OSError")
    assert ".pyatv.conf" in tools.PROTECTED


def test_closing_a_device_is_not_goodbye():
    """'Televizyonu kapat' Nova'yı kapatmaz; komut olarak beyne gider."""
    from nova.wake import is_goodbye
    for cmd in ["Televizyonu kapat.", "TV'yi kapat", "Müziği kapat", "Nova televizyonu kapat", "Safari'yi kapat",
                "Işıkları kapat", "TV'yi kapat, teşekkürler", "Tamam, şimdi televizyonu kapat", "Müziği sustur"]:
        assert not is_goodbye(cmd), cmd
    for bye in ["Kapat.", "Tamam kapat", "Kendini kapat", "Nova kapan", "Hadi artık uyu", "Sus", "Teşekkürler Nova",
                "Çok teşekkür ederim", "Sağ ol", "Görüşürüz", "Tamam bu kadar", "Şu an hiçbir şey yapma, kapat.",
                "Tamam çıkış yap", "Hoşça kal Nova"]:
        assert is_goodbye(bye), bye


def test_face_emotion_follows_the_sentence():
    from nova.face import FaceBus, guess_emotion
    for text, emo in [("Televizyonu açamadım, Apple TV cevap vermedi.", "sad"), ("Maalesef bulamadım.", "sad"),
                      ("Harika, video yayında!", "excited"), ("Rica ederim efendim.", "happy"),
                      ("Vay, bin abone olmuş!", "surprised"), ("YouTube'u da açayım mı?", "curious"),
                      ("Şaka yapıyorum efendim.", "wink"), ("Saat on bire geliyor.", "neutral"), ("", "neutral")]:
        assert guess_emotion(text) == emo, text
    bus = FaceBus()
    q = bus.subscribe()
    bus.speak(None, "Televizyon açıldı, keyfine bak.")
    assert q.get_nowait()["emotion"] == "happy"          # ifade, konuşma olayıyla birlikte yüze gider


def test_face_page_is_never_served_stale():
    """Yüz güncellenince tarayıcı eski kopyayı göstermesin: adres dosyayla birlikte değişir, sayfa önbelleğe alınmaz."""
    import os
    from nova import face
    html = face.STATIC / "face.html"
    before = face.face_url()
    st = html.stat()
    try:
        os.utime(html, (st.st_atime, st.st_mtime + 60))
        assert face.face_url() != before and "&v=" in before
    finally:
        os.utime(html, (st.st_atime, st.st_mtime))


def test_own_foreign_speech_is_not_mistaken_for_stop(monkeypatch):
    """Nova Türkiyeçe konuşurken küçük model kendi sesinde "dur" duysa da söz kesilmez: büyük model doğrulamaz."""
    from nova import wake
    heard = {"tiny": "Dur.", "big": "Jag öppnar och stänger program, hittar och läser filer."}
    monkeypatch.setattr(wake.voice, "transcribe", lambda a, model=None, prompt=None: heard["tiny" if model else "big"])
    stopped = []

    class Sp:
        interrupted, current, recent = False, "Jag kör kommandon i terminalen.", ["Hej, jag heter Nova."]
        def interrupt(self): stopped.append(1); self.interrupted = True

    class OneShotMic:
        n = 0
        def flush(self): pass
        def segment(self, stop=None, **kw):
            self.n += 1
            if self.n > 1:
                stop.set()
                return None
            return np.ones(8000, np.float32)

    wake.BargeIn(OneShotMic(), Sp()).run()
    assert stopped == []
    heard.update(tiny="Nova.", big="Nova.")          # ilk cümlede adını söylemişti: kendi sesi sayılır
    wake.BargeIn(OneShotMic(), Sp()).run()
    assert stopped == []
    heard.update(tiny="Dur dur.", big="Nova dur.")   # gerçek, kısa bir "dur": keser
    wake.BargeIn(OneShotMic(), Sp()).run()
    assert stopped == [1]
    monkeypatch.setattr(config, "BARGE_IN", False)     # kapalıyken hiç dinlemez
    wake.BargeIn(OneShotMic(), Sp()).run()
    assert stopped == [1]
    assert wake.is_stop_command("Yeter Nova") and not wake.is_stop_command("bu kadar bilgi bana yeter de artar bile")


def test_project_notes_come_from_knowledge_folder(tmp_path, monkeypatch):
    """Proje bilgisi koda gömülü değildir: knowledge/ klasörüne konan her .md dosyası bir proje olur."""
    kb = tmp_path / "knowledge"; kb.mkdir()
    (kb / "kitap-kulubu.md").write_text("Kitap Kulübü: üyelerin her ay bir kitap seçtiği mobil uygulama.")
    monkeypatch.setattr(config, "KNOWLEDGE_DIR", kb)
    assert tools.knowledge_slugs() == ["kitap-kulubu"]
    assert "her ay bir kitap" in tools.project_info("Kitap Kulubu uygulaması")
    assert "Bilinen projeler: kitap-kulubu" in tools.project_info("bambaşka")
    client, seen = fake_ollama([{"role": "assistant", "content": "Üyeler her ay bir kitap seçiyor."}])
    Agent(client).ask("kitap kulubu ne yapıyor?")                 # lokal modele not kendiliğinden verilir
    assert any("her ay bir kitap" in m["content"] for m in seen[0]["messages"] if m["role"] == "system")


def test_owner_and_city_come_from_settings(monkeypatch):
    from nova.agent import system_prompt
    monkeypatch.setattr(config, "OWNER", "Deniz"); monkeypatch.setattr(config, "HOME_CITY", "İzmir")
    p = system_prompt()
    assert "Kullanıcının adı Deniz." in p and "İzmir şehrinde yaşıyor." in p
