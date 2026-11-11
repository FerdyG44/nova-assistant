"""Nova'nın Mac üzerindeki elleri: uygulamalar, dosyalar, ekran, klavye, medya, takvim, hatırlatıcılar.

macOS izinleri (bir kere, Sistem Ayarları → Gizlilik ve Güvenlik):
  - Erişilebilirlik  : tıklama ve klavye (type_text, press_keys, click_on_screen)
  - Ekran Kaydı      : ekranı okuma (read_screen, click_on_screen)
  - Otomasyon        : Müzik, Takvim, Hatırlatıcılar, Finder, Mail kontrolü (ilk kullanımda macOS kendisi sorar)
İzinler Nova'yı başlattığın uygulamaya verilir (Terminal ya da python).
"""
from __future__ import annotations

import html
import re
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from . import config
from .tools import _home_path, _run, tool

RISKY_WORDS = re.compile(r"(gönder|sil|satın|öde|yayınla|paylaş|onayla|kapat|send|delete|remove|buy|pay|"
                         r"publish|post|share|confirm|purchase|checkout|erase|format)", re.I)


def osa(script: str, timeout: int = 30) -> str:
    """AppleScript çalıştırır, çıktısını ya da hatasını döner."""
    try:
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return "Hata: osascript yok (bu bir Mac değil mi?)"
    except subprocess.TimeoutExpired:
        return "Hata: zaman aşımı"
    if p.returncode != 0:
        err = p.stderr.strip()
        if "-1743" in err or "Not authorized" in err:
            return "Hata: Otomasyon izni yok. Sistem Ayarları → Gizlilik ve Güvenlik → Otomasyon'dan izin ver."
        if "-25211" in err or "assistive" in err.lower():
            return "Hata: Erişilebilirlik izni yok. Sistem Ayarları → Gizlilik ve Güvenlik → Erişilebilirlik'ten izin ver."
        return f"Hata: {err[:300]}"
    return p.stdout.strip() or "tamam"


def _q(s: str) -> str:
    """AppleScript metin kaçışı."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------- uygulamalar
@tool("open_app", "Bir uygulamayı aç ya da öne getir (örn. Safari, Xcode, Spotify, Notlar, Finder).",
      {"name": {"type": "string"}}, ["name"])
def open_app(name: str) -> str:
    out = _run(["open", "-a", name])
    return f"{name} açıldı." if out.startswith("(çıktı yok") else out


@tool("quit_app", "Bir uygulamayı kapat (kaydedilmemiş iş kaybolabilir).", {"name": {"type": "string"}}, ["name"],
      confirm=lambda a: True, summary=lambda a: f"{a.get('name')} uygulamasını kapat")
def quit_app(name: str) -> str:
    return osa(f"tell application {_q(name)} to quit")


@tool("running_apps", "Şu an açık olan uygulamalar ve öndeki uygulama.")
def running_apps() -> str:
    front = osa('tell application "System Events" to get name of first application process whose frontmost is true')
    apps = osa('tell application "System Events" to get name of every application process whose background only is false')
    return f"Öndeki: {front}\nAçık: {apps}"


@tool("open_url", "Bir web adresini ya da dosyayı varsayılan uygulamada aç.", {"target": {"type": "string"}}, ["target"])
def open_url(target: str) -> str:
    if not re.match(r"^[a-z]+://", target) and not target.startswith(("/", "~")):
        target = "https://" + target
    if target.startswith(("/", "~")):
        target = str(_home_path(target))
    out = _run(["open", target])
    return "Açıldı." if out.startswith("(çıktı yok") else out


@tool("read_webpage", "Bir web sayfasının metnini oku (haber, makale, doküman). web_search'ten gelen linklerle kullan.",
      {"url": {"type": "string"}}, ["url"])
def read_webpage(url: str) -> str:
    import httpx
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 Nova"})
    except Exception as e:
        return f"Sayfa açılamadı: {e}"
    t = re.sub(r"(?is)<(script|style|nav|footer|header|noscript|svg).*?</\1>", " ", r.text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(t)).strip()
    return t[:6000] or "Sayfada okunabilir metin yok."


# ---------------------------------------------------------------- dosyalar
@tool("find_files", "Mac'te dosya ara (Spotlight). İsimle ya da içerikle bulur.",
      {"query": {"type": "string"}, "in_content": {"type": "boolean", "description": "içerikte de ara"}}, ["query"])
def find_files(query: str, in_content: bool = False) -> str:
    args = ["mdfind", "-onlyin", str(config.HOME_DIR)] + ([query] if in_content else ["-name", query])
    out = _run(args, timeout=30)
    lines = [l for l in out.splitlines() if "/Library/" not in l and "/." not in l][:25]
    return "\n".join(lines) or "Bulunamadı."


@tool("list_folder", "Bir klasörün içeriğini listele.", {"path": {"type": "string"}}, ["path"])
def list_folder(path: str) -> str:
    p = _home_path(path)
    if not p.is_dir():
        return "Klasör yok."
    items = sorted(p.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)[:60]
    return "\n".join(f"{'[klasör] ' if i.is_dir() else ''}{i.name}" for i in items if not i.name.startswith(".")) or "Boş."


def _exists(a: dict, key: str) -> bool:
    try:
        return _home_path(a.get(key, "")).exists()
    except Exception:
        return True


@tool("write_file", "Dosya oluştur ya da sonuna ekle. Var olan bir dosyanın üzerine yazmak onay ister.",
      {"path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}}, ["path", "content"],
      confirm=lambda a: not a.get("append") and _exists(a, "path"),
      summary=lambda a: f"{a.get('path')} dosyasının üzerine yaz")
def write_file(path: str, content: str, append: bool = False) -> str:
    p = _home_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w") as f:
        f.write(content)
    return f"{'Eklendi' if append else 'Yazıldı'}: {p}"


@tool("move_file", "Dosya/klasör taşı ya da yeniden adlandır. Hedef varsa onay ister.",
      {"source": {"type": "string"}, "destination": {"type": "string"}}, ["source", "destination"],
      confirm=lambda a: _exists(a, "destination") and not Path(str(a.get("destination"))).expanduser().is_dir(),
      summary=lambda a: f"{a.get('source')} → {a.get('destination')} (hedefin üzerine yazılacak)")
def move_file(source: str, destination: str) -> str:
    src, dst = _home_path(source), _home_path(destination)
    if not src.exists():
        return "Kaynak yok."
    if dst.is_dir():
        dst = dst / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return f"Taşındı: {dst}"


@tool("trash_file", "Dosya ya da klasörü Çöp Sepeti'ne at (geri alınabilir).", {"path": {"type": "string"}}, ["path"],
      confirm=lambda a: True, summary=lambda a: f"{a.get('path')} çöpe atılsın")
def trash_file(path: str) -> str:
    p = _home_path(path)
    if not p.exists():
        return "Dosya yok."
    return osa(f'tell application "Finder" to delete (POSIX file {_q(str(p))} as alias)')


# ---------------------------------------------------------------- pano
@tool("clipboard_read", "Panodaki (kopyalanan) metni oku.")
def clipboard_read() -> str:
    return _run(["pbpaste"])[:4000]


@tool("clipboard_write", "Metni panoya kopyala.", {"text": {"type": "string"}}, ["text"])
def clipboard_write(text: str) -> str:
    subprocess.run(["pbcopy"], input=text, text=True, check=False)
    return "Panoya kopyalandı."


# ---------------------------------------------------------------- ekran: okuma, tıklama, klavye
def _screenshot() -> Path:
    path = Path(tempfile.gettempdir()) / "nova_screen.png"
    subprocess.run(["screencapture", "-x", "-m", str(path)], check=True, timeout=15)
    return path


def ocr(path: Path) -> list[tuple[str, float, float, float, float]]:
    """macOS Vision ile metin tanıma. (metin, x, y, w, h) — normalize, sol üst köşe orijinli."""
    import Vision
    from Foundation import NSURL
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    try:
        langs, _ = req.supportedRecognitionLanguagesAndReturnError_(None)
        req.setRecognitionLanguages_([l for l in ("tr-TR", "en-US", "sv-SE") if l in (langs or [])] or ["en-US"])
    except Exception:
        pass
    req.setUsesLanguageCorrection_(True)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(NSURL.fileURLWithPath_(str(path)), None)
    handler.performRequests_error_([req], None)
    out = []
    for obs in req.results() or []:
        c = obs.topCandidates_(1)
        if not c:
            continue
        bb = obs.boundingBox()
        out.append((str(c[0].string()), bb.origin.x, 1 - bb.origin.y - bb.size.height, bb.size.width, bb.size.height))
    out.sort(key=lambda t: (round(t[2], 2), t[1]))
    return out


@tool("read_screen", "Ekranda şu an ne yazdığını oku (OCR). 'Ekranda ne var', 'şunu oku' gibi isteklerde kullan.")
def read_screen() -> str:
    try:
        items = ocr(_screenshot())
    except Exception as e:
        return f"Hata: ekran okunamadı ({e}). Ekran Kaydı iznini kontrol et."
    front = osa('tell application "System Events" to get name of first application process whose frontmost is true')
    text = "\n".join(t[0] for t in items)
    return f"Öndeki uygulama: {front}\n{text[:5000]}" if text else f"Öndeki uygulama: {front}\n(ekranda metin yok)"


def _click(x: float, y: float, double: bool = False) -> None:
    import Quartz
    pt = Quartz.CGPointMake(x, y)
    for _ in range(2 if double else 1):
        for kind in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
            ev = Quartz.CGEventCreateMouseEvent(None, kind, pt, Quartz.kCGMouseButtonLeft)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
            time.sleep(0.05)


@tool("click_on_screen", "Ekranda üzerinde verilen yazı olan düğmeye/bağlantıya tıkla (OCR ile bulur).",
      {"text": {"type": "string", "description": "tıklanacak yazı, örn. 'Kaydet'"},
       "double": {"type": "boolean"}}, ["text"],
      confirm=lambda a: bool(RISKY_WORDS.search(a.get("text", ""))),
      summary=lambda a: f"Ekranda '{a.get('text')}' yazısına tıkla")
def click_on_screen(text: str, double: bool = False) -> str:
    import Quartz
    try:
        items = ocr(_screenshot())
    except Exception as e:
        return f"Hata: ekran okunamadı ({e}). Ekran Kaydı iznini kontrol et."
    want = text.lower().strip()
    hits = [t for t in items if t[0].lower().strip() == want] or [t for t in items if want in t[0].lower()]
    if not hits:
        return f"Ekranda '{text}' bulamadım. Görünen yazılar: " + ", ".join(t[0] for t in items[:30])
    s, x, y, w, h = hits[0]
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    _click(b.origin.x + (x + w / 2) * b.size.width, b.origin.y + (y + h / 2) * b.size.height, double)
    return f"'{s}' yazısına tıkladım." + (f" ({len(hits)} eşleşme vardı, ilkini seçtim)" if len(hits) > 1 else "")


@tool("type_text", "Öndeki uygulamada imlecin olduğu yere metin yaz (Türkçe karakterler dahil).",
      {"text": {"type": "string"}, "press_enter": {"type": "boolean"}}, ["text"])
def type_text(text: str, press_enter: bool = False) -> str:
    old = _run(["pbpaste"])
    subprocess.run(["pbcopy"], input=text, text=True, check=False)
    out = osa('tell application "System Events" to keystroke "v" using {command down}')
    time.sleep(0.3)
    if press_enter and not out.startswith("Hata"):
        out = osa('tell application "System Events" to key code 36')
    subprocess.run(["pbcopy"], input=old, text=True, check=False)
    return "Yazdım." if not out.startswith("Hata") else out


KEY_CODES = {"enter": 36, "return": 36, "tab": 48, "space": 49, "esc": 53, "escape": 53, "delete": 51,
             "backspace": 51, "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119,
             "pageup": 116, "pagedown": 121, "f5": 96}
MODS = {"cmd": "command down", "command": "command down", "ctrl": "control down", "control": "control down",
        "alt": "option down", "option": "option down", "shift": "shift down"}


@tool("press_keys", "Klavye kısayolu bas, örn. 'cmd+s', 'cmd+t', 'enter', 'cmd+shift+4', 'esc'.",
      {"keys": {"type": "string"}}, ["keys"],
      confirm=lambda a: a.get("keys", "").lower().replace(" ", "") in ("cmd+q", "cmd+alt+esc", "cmd+delete",
                                                                         "cmd+shift+delete", "ctrl+cmd+q"),
      summary=lambda a: f"{a.get('keys')} kısayoluna bas")
def press_keys(keys: str) -> str:
    parts = [p.strip().lower() for p in keys.replace("-", "+").split("+") if p.strip()]
    mods = [MODS[p] for p in parts if p in MODS]
    key = [p for p in parts if p not in MODS]
    if len(key) != 1:
        return "Hata: tek bir tuş olmalı, örn. cmd+s"
    k = key[0]
    using = f" using {{{', '.join(mods)}}}" if mods else ""
    stroke = f"key code {KEY_CODES[k]}" if k in KEY_CODES else f"keystroke {_q(k)}"
    out = osa(f'tell application "System Events" to {stroke}{using}')
    return f"{keys} basıldı." if not out.startswith("Hata") else out


# ---------------------------------------------------------------- ses ve medya
@tool("set_volume", "Mac'in ses seviyesini ayarla (0-100) ya da sessize al.",
      {"level": {"type": "integer"}, "mute": {"type": "boolean"}})
def set_volume(level: int | None = None, mute: bool | None = None) -> str:
    if mute is not None:
        return osa(f"set volume {'with' if mute else 'without'} output muted")
    if level is None:
        return osa("output volume of (get volume settings)")
    out = osa(f"set volume output volume {max(0, min(int(level), 100))}")
    return out if out.startswith("Hata") else f"Ses seviyesi %{level}."


@tool("media_control", "Müzik kontrolü: play, pause, next, previous, now_playing. Spotify ya da Music.",
      {"action": {"type": "string", "enum": ["play", "pause", "next", "previous", "now_playing"]},
       "app": {"type": "string", "description": "Spotify ya da Music; boşsa açık olanı kullanır"}}, ["action"])
def media_control(action: str, app: str = "") -> str:
    if not app:
        running = osa('tell application "System Events" to get name of every application process')
        app = "Spotify" if "Spotify" in running else "Music"
    if action == "now_playing":
        return osa(f'tell application {_q(app)}\n if player state is playing then\n'
                   f'  return (name of current track) & " — " & (artist of current track)\n end if\nend tell\n'
                   f'return "çalan bir şey yok"')
    cmd = {"play": "play", "pause": "pause", "next": "next track", "previous": "previous track"}[action]
    return osa(f"tell application {_q(app)} to {cmd}")


@tool("play_music", "Spotify'da şarkı/sanatçı/çalma listesi aç ve çal.", {"query": {"type": "string"}}, ["query"])
def play_music(query: str) -> str:
    from urllib.parse import quote
    _run(["open", f"spotify:search:{quote(query)}"])
    time.sleep(2.5)
    return ("Spotify'da aramayı açtım. Çalmak için ilk sonuca tıklamam gerekirse söyle; "
            "ya da media_control play ile devam ettirebilirim.")


# ---------------------------------------------------------------- takvim, hatırlatıcılar, bildirim, zamanlayıcı
@tool("calendar_events", "Takvimdeki etkinlikler (bugün ya da önümüzdeki N gün).",
      {"days": {"type": "integer", "description": "0 = bugün, 1 = yarına kadar…"}})
def calendar_events(days: int = 0) -> str:
    script = f'''
set startD to current date
set hours of startD to 0
set minutes of startD to 0
set seconds of startD to 0
set endD to startD + ({int(days) + 1} * days)
set out to ""
tell application "Calendar"
  repeat with c in calendars
    try
      repeat with e in (every event of c whose start date ≥ startD and start date < endD)
        set out to out & ((start date of e) as string) & " | " & (summary of e) & linefeed
      end repeat
    end try
  end repeat
end tell
return out'''
    out = osa(script, timeout=60)
    return out if out and out != "tamam" else "Takvimde etkinlik yok."


@tool("add_calendar_event", "Takvime etkinlik ekle.",
      {"title": {"type": "string"}, "start": {"type": "string", "description": "YYYY-MM-DD HH:MM"},
       "minutes": {"type": "integer", "description": "süre, varsayılan 60"}}, ["title", "start"])
def add_calendar_event(title: str, start: str, minutes: int = 60) -> str:
    import datetime as dt
    try:
        s = dt.datetime.strptime(start.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return "Hata: başlangıç 'YYYY-MM-DD HH:MM' biçiminde olmalı."
    script = f'''
set d to current date
set year of d to {s.year}
set month of d to {s.month}
set day of d to {s.day}
set hours of d to {s.hour}
set minutes of d to {s.minute}
set seconds of d to 0
tell application "Calendar"
  tell (first calendar whose writable is true)
    make new event with properties {{summary:{_q(title)}, start date:d, end date:d + ({int(minutes)} * minutes)}}
  end tell
end tell'''
    out = osa(script, timeout=60)
    return f"Takvime eklendi: {title}, {start}." if not out.startswith("Hata") else out


@tool("add_reminder", "Hatırlatıcılar uygulamasına hatırlatma ekle (iPhone'a da senkronlanır).",
      {"title": {"type": "string"}, "due": {"type": "string", "description": "YYYY-MM-DD HH:MM (isteğe bağlı)"}},
      ["title"])
def add_reminder(title: str, due: str = "") -> str:
    props = f"name:{_q(title)}"
    pre = ""
    if due:
        import datetime as dt
        try:
            s = dt.datetime.strptime(due.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            return "Hata: tarih 'YYYY-MM-DD HH:MM' biçiminde olmalı."
        pre = (f"set d to current date\nset year of d to {s.year}\nset month of d to {s.month}\nset day of d to {s.day}\n"
               f"set hours of d to {s.hour}\nset minutes of d to {s.minute}\nset seconds of d to 0\n")
        props += ", remind me date:d"
    out = osa(f'{pre}tell application "Reminders" to make new reminder with properties {{{props}}}')
    return f"Hatırlatma eklendi: {title}" + (f" ({due})" if due else "") if not out.startswith("Hata") else out


@tool("list_reminders", "Tamamlanmamış hatırlatmaları listele.")
def list_reminders() -> str:
    out = osa('tell application "Reminders" to get name of (every reminder whose completed is false)', timeout=60)
    return out if out != "tamam" else "Bekleyen hatırlatma yok."


@tool("notify", "Mac'te bildirim göster.", {"text": {"type": "string"}}, ["text"])
def notify(text: str) -> str:
    return osa(f'display notification {_q(text)} with title "Nova" sound name "Glass"')


_TIMERS: list[threading.Timer] = []


@tool("set_timer", "Zamanlayıcı kur; süre dolunca Nova sesli olarak hatırlatır.",
      {"minutes": {"type": "number"}, "label": {"type": "string"}}, ["minutes"])
def set_timer(minutes: float, label: str = "") -> str:
    def fire():
        from . import voice
        msg = f"Efendim, {label} için süre doldu." if label else "Efendim, zamanlayıcı doldu."
        notify(msg)
        audio = voice.speak(msg)
        if audio:
            subprocess.run(["afplay", str(config.AUDIO_DIR / audio)], check=False)
    t = threading.Timer(float(minutes) * 60, fire)
    t.daemon = True
    t.start()
    _TIMERS.append(t)
    return f"{minutes:g} dakikalık zamanlayıcı kuruldu" + (f": {label}" if label else "") + "."


# ---------------------------------------------------------------- e-posta (gönderme yok, taslak açar)
@tool("draft_email", "Mail uygulamasında e-posta taslağı aç (göndermez; kullanıcı kontrol edip kendisi gönderir).",
      {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}, ["subject", "body"])
def draft_email(subject: str, body: str, to: str = "") -> str:
    rcpt = (f'\n make new to recipient at end of to recipients with properties {{address:{_q(to)}}}' if to else "")
    out = osa(f'tell application "Mail"\n set m to make new outgoing message with properties '
              f'{{subject:{_q(subject)}, content:{_q(body)}, visible:true}}\n tell m{rcpt}\n end tell\n activate\nend tell')
    return "Taslak Mail'de açıldı, kontrol edip gönderebilirsin." if not out.startswith("Hata") else out


@tool("run_applescript", "Özel bir AppleScript çalıştır (diğer araçların yetmediği Mac otomasyonları için).",
      {"script": {"type": "string"}}, ["script"],
      confirm=lambda a: True, summary=lambda a: "AppleScript çalıştır: " + a.get("script", "")[:120])
def run_applescript(script: str) -> str:
    return osa(script, timeout=60)
