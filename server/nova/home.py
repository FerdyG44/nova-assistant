"""Nova'nın evdeki elleri: Apple TV (ve ona HDMI ile bağlı televizyon).

Apple TV uyanınca HDMI-CEC ile televizyonu da açar, kapanınca kapatır. Bu yüzden televizyonun
markası önemli değil; Nova yalnızca Apple TV ile konuşur (pyatv kütüphanesi, yerel ağ üzerinden).

Bir kereye mahsus eşleştirme (ekranda çıkan PIN'i girersin):
    cd ~/projects/nova/server && .venv/bin/atvremote wizard
Eşleştirme bilgisi ~/.pyatv.conf dosyasına kaydedilir ve Nova onu oradan okur.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from . import config
from .tools import tool

log = logging.getLogger("nova.home")

PAIR_HELP = ("Apple TV henüz Nova ile eşleştirilmemiş. Terminalde şunu çalıştır ve ekranda çıkan PIN'i gir: "
             "cd ~/projects/nova/server && .venv/bin/atvremote wizard")
CACHE = config.DATA_DIR / "appletv.json"          # son bulunan Apple TV'nin adresi: sonraki bağlantılar hızlı olur

MUSIC_APPS = ("com.spotify.client", "com.apple.TVMusic")    # yüklüyse önce Spotify, yoksa Apple Music
REMOTE = ("play", "pause", "play_pause", "stop", "next", "previous", "up", "down", "left", "right",
          "select", "menu", "home", "volume_up", "volume_down")


class TVError(Exception):
    """Kullanıcıya aynen söylenebilecek, anlaşılır hata."""


def _cached_host() -> str | None:
    try:
        return json.loads(CACHE.read_text()).get("address")
    except Exception:
        return None


async def _find(pyatv, loop, storage):
    """Ağdaki Apple TV'yi bulur. Önce bilinen adrese sorar (hızlı), olmazsa tüm ağı tarar."""
    from pyatv.const import OperatingSystem

    def pick(confs):
        tvs = [c for c in confs if c.device_info.operating_system == OperatingSystem.TvOS]
        if config.APPLETV_NAME:
            tvs = [c for c in tvs if config.APPLETV_NAME.lower() in c.name.lower()]
        return tvs[0] if tvs else None

    host = _cached_host()
    conf = pick(await pyatv.scan(loop, hosts=[host], timeout=3, storage=storage)) if host else None
    if conf is None:
        conf = pick(await pyatv.scan(loop, timeout=5, storage=storage))
    if conf is None:
        raise TVError("Ağda Apple TV bulamadım. Apple TV ile bu Mac aynı Wi-Fi'da mı?"
                      + (f" (aranan isim: {config.APPLETV_NAME})" if config.APPLETV_NAME else ""))
    try:
        CACHE.write_text(json.dumps({"name": conf.name, "address": str(conf.address)}))
    except OSError:
        pass
    return conf


async def _connect():
    """Eşleştirilmiş Apple TV'ye bağlanır ve bağlantıyı döner."""
    try:
        import pyatv
        from pyatv.const import Protocol
        from pyatv.storage.file_storage import FileStorage
    except ImportError:
        raise TVError("pyatv kurulu değil. Nova'yı start.sh ile yeniden başlat, eksik paketi kendisi kurar.")
    loop = asyncio.get_running_loop()
    storage = FileStorage.default_storage(loop)       # ~/.pyatv.conf (atvremote wizard buraya yazar)
    await storage.load()
    conf = await _find(pyatv, loop, storage)
    companion = conf.get_service(Protocol.Companion)
    if companion is None or not companion.credentials:
        raise TVError(PAIR_HELP)
    return await pyatv.connect(conf, loop, storage=storage)


async def _session(action, timeout: float):
    atv = await asyncio.wait_for(_connect(), 15)
    try:
        return await asyncio.wait_for(action(atv), timeout)
    finally:
        pending = atv.close()
        if pending:
            await asyncio.wait(pending, timeout=3)


def _tv(action, timeout: float = 20) -> str:
    """Apple TV'ye bağlanıp `action(atv)` çalıştırır. Her hata, modele okunabilir bir cümle olarak döner."""
    try:
        return asyncio.run(_session(action, timeout))
    except TVError as e:
        return str(e)
    except asyncio.TimeoutError:
        return "Apple TV zamanında cevap vermedi. Açık ve aynı ağda mı?"
    except Exception as e:
        log.warning("Apple TV hatası: %s: %s", type(e).__name__, e)
        if "auth" in type(e).__name__.lower() or "pair" in str(e).lower():
            return PAIR_HELP
        return f"Apple TV hatası: {type(e).__name__}: {str(e)[:160]}"


async def _ensure_on(atv) -> bool:
    """Apple TV kapalıysa açar (televizyon da HDMI-CEC ile açılır). Zaten açıksa False döner."""
    from pyatv.const import PowerState
    if atv.power.power_state == PowerState.On:
        return False
    await atv.power.turn_on()                        # Companion "await_new_state" desteklemez: durumu kendimiz bekleriz
    for _ in range(16):                              # en çok ~4 sn; durum bildirimi gecikirse de komut gitmiştir
        if atv.power.power_state == PowerState.On:
            break
        await asyncio.sleep(0.25)
    return True


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower().replace("ı", "i").replace("ü", "u").replace("ö", "o")
                  .replace("ş", "s").replace("ç", "c").replace("ğ", "g").replace("+", "plus"))


def _match_app(apps, name: str):
    """Yüklü uygulamalar içinden isme en çok benzeyeni bulur ('netflix', 'you tube', 'apple music'…)."""
    want = _norm(name)
    alias = {"muzik": "music", "applemusic": "music", "applemuzik": "music", "appletv": "tv", "ayarlar": "settings",
             "fotograflar": "photos", "filmler": "movies"}
    want = alias.get(want, want)
    if not want:
        return None
    exact = [a for a in apps if _norm(a.name or "") == want]
    if exact:
        return exact[0]
    close = [a for a in apps if want in _norm(a.name or "") or want in _norm(a.identifier or "")]
    return min(close, key=lambda a: len(a.name or "")) if close else None


# ---------------------------------------------------------------- araçlar
@tool("tv_power", "Televizyonu aç ya da kapat (Apple TV üzerinden; Apple TV açılınca televizyon da açılır). "
      "'Televizyonu aç/kapat', 'TV'yi aç' gibi isteklerde kullan.",
      {"state": {"type": "string", "enum": ["on", "off"]}}, ["state"])
def tv_power(state: str) -> str:
    async def act(atv):
        if state == "off":
            await atv.power.turn_off()
            return "Apple TV ve televizyon kapatıldı."
        return "Televizyon açıldı." if await _ensure_on(atv) else "Televizyon zaten açık."
    return _tv(act)


@tool("tv_status", "Televizyonun/Apple TV'nin durumu: açık mı, hangi uygulama açık, şu an ne çalıyor.")
def tv_status() -> str:
    async def act(atv):
        from pyatv.const import DeviceState, PowerState
        if atv.power.power_state == PowerState.Off:
            return "Apple TV kapalı (uyku modunda)."
        lines = ["Apple TV açık." if atv.power.power_state == PowerState.On
                 else "Apple TV'ye bağlandım ama açık mı kapalı mı olduğunu bildirmedi."]
        try:
            app = atv.metadata.app
            if app and app.name:
                lines.append(f"Açık uygulama: {app.name}")
        except Exception:
            pass
        p = await atv.metadata.playing()
        state = {DeviceState.Playing: "çalıyor", DeviceState.Paused: "duraklatıldı"}.get(p.device_state)
        if state and p.title:
            what = " - ".join(x for x in (p.artist, p.title) if x)
            lines.append(f"Şu an: {what} ({state})")
        else:
            lines.append("Şu an bir şey çalmıyor.")
        return "\n".join(lines)
    return _tv(act)


@tool("tv_open_app", "Apple TV'de bir uygulama aç (YouTube, Netflix, Spotify, Müzik, Disney+…). "
      "Televizyon kapalıysa önce açar.", {"name": {"type": "string"}}, ["name"])
def tv_open_app(name: str) -> str:
    async def act(atv):
        await _ensure_on(atv)
        apps = await atv.apps.app_list()
        app = _match_app(apps, name)
        if app is None:
            names = ", ".join(sorted(a.name for a in apps if a.name))
            return f"Apple TV'de '{name}' diye bir uygulama yok. Yüklü olanlar: {names}"
        await atv.apps.launch_app(app.identifier)
        return f"Televizyonda {app.name} açıldı."
    return _tv(act, 30)


@tool("tv_remote", "Apple TV kumandası: play, pause, play_pause, stop, next, previous, up, down, left, right, "
      "select, menu (geri), home, volume_up, volume_down. Televizyonda oynatmayı durdurmak/devam ettirmek, "
      "şarkı geçmek, sesi açıp kısmak ya da menüde gezinmek için.",
      {"command": {"type": "string", "enum": list(REMOTE)},
       "times": {"type": "integer", "description": "kaç kez basılsın (varsayılan 1, en çok 10)"}}, ["command"])
def tv_remote(command: str, times: int = 1) -> str:
    if command not in REMOTE:
        return f"Bilinmeyen kumanda tuşu: {command}. Geçerli olanlar: {', '.join(REMOTE)}"
    n = max(1, min(int(times or 1), 10))

    async def act(atv):
        target = atv.audio if command.startswith("volume_") else atv.remote_control
        for i in range(n):
            await getattr(target, command)()
            if i < n - 1:
                await asyncio.sleep(0.25)
        return f"Kumanda: {command}" + (f" x{n}" if n > 1 else "") + " gönderildi."
    return _tv(act)


@tool("tv_play_music", "Televizyonda (Apple TV üzerinden) müzik başlat: müzik uygulamasını açar ve oynat komutu "
      "gönderir; kaldığı yerden ya da son listeden devam eder. Belirli bir şarkıyı arayamaz. "
      "'Müzik aç', 'televizyonda müzik çal' gibi isteklerde kullan.")
def tv_play_music() -> str:
    async def act(atv):
        woke = await _ensure_on(atv)
        apps = await atv.apps.app_list()
        app = _match_app(apps, config.TV_MUSIC_APP) if config.TV_MUSIC_APP else None
        if app is None:
            by_id = {a.identifier: a for a in apps}
            app = next((by_id[i] for i in MUSIC_APPS if i in by_id), None)
        if app is None:
            return "Apple TV'de Spotify ya da Müzik uygulaması bulamadım."
        if woke:
            await asyncio.sleep(3)                   # televizyonun açılmasını bekle
        await atv.apps.launch_app(app.identifier)
        await asyncio.sleep(4)                       # uygulama açılsın
        await atv.remote_control.play()
        return f"Televizyonda {app.name} açıldı ve oynat komutu gönderildi."
    return _tv(act, 40)
