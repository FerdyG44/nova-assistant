"""Yüz ekranı: tarayıcıda tam ekran açılan piksel yüz ile sunucu arasındaki köprü.

Sunucu -> yüz : Server-Sent Events (/face/events)  {type: state|heard|speak|close, ...}
Yüz -> sunucu : /face/ended  (ses bitti; dinleyici mikrofonu tekrar açabilir)
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, StreamingResponse

from . import config

STATIC = Path(__file__).parent / "static"
PROFILE = config.DATA_DIR / "face-browser-profile"   # ayrı profil: kapatırken sadece bu pencere kapanır


# Cümleden yüz ifadesi: basit ama öngörülebilir. Sıra önemli; ilk eşleşen kazanır.
# face.html'deki EMO tablosuyla aynı isimler: neutral, happy, excited, sad, surprised, curious, wink.
_EMOTIONS = [
    ("sad", re.compile(r"maalesef|üzgünüm|özür|kusura bakma|yapamadım|açamadım|kapatamadım|bulamadım|ulaşamadım|"
                       r"olmadı|başaramadım|başarısız|hata verdi|hata oluştu|ne yazık|üzüldüm|geçmiş olsun", re.I)),
    ("wink", re.compile(r"şaka|takılıyorum|espri|aramızda kalsın", re.I)),
    ("surprised", re.compile(r"\bvay\b|inanılmaz|şaşırtıcı|şaşırdım|yok artık|cidden mi|gerçekten mi|hayret", re.I)),
    ("excited", re.compile(r"harika|süper|mükemmel|tebrik|müthiş|şahane|bravo|haha|hehe", re.I)),
    ("happy", re.compile(r"rica ederim|memnuniyetle|seve seve|sevindim|güzel haber|tamamdır|hallettim|günaydın|"
                         r"merhaba|hoş geldin|iyi geceler|afiyet olsun|keyfine bak|ne güzel|teşekkür", re.I)),
]


def guess_emotion(text: str) -> str:
    """Nova'nın söyleyeceği cümleye yakışan yüz ifadesi."""
    t = text or ""
    for name, pattern in _EMOTIONS:
        if pattern.search(t):
            return name
    if t.rstrip().endswith("?"):
        return "curious"
    if t.rstrip().endswith("!"):
        return "happy"
    return "neutral"


class FaceBus:
    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()
        self.ended = threading.Event()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @property
    def connected(self) -> bool:
        return bool(self._subs)

    def publish(self, **event) -> None:
        with self._lock:
            for q in self._subs:
                q.put(event)

    # --- kısayollar
    def state(self, s: str) -> None:
        self.publish(type="state", state=s)

    def speak(self, audio: str | None, text: str, timeout: float = 90, more: bool = False) -> None:
        """Yüze sesi çaldırır ve bitene kadar bekler (Nova kendi sesini dinlemesin diye).
        more=True: arkasından başka cümle gelecek, yüz konuşma durumunda kalsın."""
        self.ended.clear()
        self.publish(type="speak", audio=audio, text=text, more=more, emotion=guess_emotion(text))
        if audio and self.connected:
            self.ended.wait(timeout)


bus = FaceBus()


# ---------------------------------------------------------------- pencereyi aç / kapat
def face_url() -> str:
    # v = dosyanın değişme zamanı: face.html güncellenince tarayıcı eski kopyayı önbellekten açmasın
    version = int((STATIC / "face.html").stat().st_mtime)
    return f"http://127.0.0.1:{config.PORT}/face?t={config.TOKEN}&v={version}"


def is_open() -> bool:
    return subprocess.run(["pgrep", "-f", str(PROFILE)], capture_output=True).returncode == 0


def open_face(wait: float = 6) -> None:
    if not is_open():
        subprocess.Popen(["open", "-na", config.BROWSER, "--args", f"--user-data-dir={PROFILE}",
                          "--kiosk", f"--app={face_url()}", "--no-first-run", "--no-default-browser-check",
                          "--autoplay-policy=no-user-gesture-required"])
    t0 = time.time()
    while not bus.connected and time.time() - t0 < wait:
        time.sleep(0.1)


def close_face() -> None:
    bus.publish(type="close")
    time.sleep(0.6)  # kapanış animasyonu
    subprocess.run(["pkill", "-f", str(PROFILE)], capture_output=True)


# ---------------------------------------------------------------- HTTP
def router(auth_dep) -> APIRouter:
    r = APIRouter()

    @r.get("/face", dependencies=[Depends(auth_dep)])
    def page():
        return FileResponse(STATIC / "face.html", media_type="text/html", headers={"Cache-Control": "no-store"})

    @r.get("/face/events", dependencies=[Depends(auth_dep)])
    async def events():
        async def gen():
            q = bus.subscribe()
            try:
                yield f"data: {json.dumps({'type': 'hello', 'name': config.NAME})}\n\n"
                while True:
                    try:
                        ev = await asyncio.to_thread(q.get, True, 15)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            finally:
                bus.unsubscribe(q)
        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @r.post("/face/ended", dependencies=[Depends(auth_dep)])
    def ended():
        bus.ended.set()
        return {"ok": True}

    return r
