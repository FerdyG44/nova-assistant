"""Nova HTTP sunucusu. Telefon (Flutter) bununla Tailscale üzerinden konuşur; Mac'teki yüz ekranı da buradan beslenir.

Çalıştır:  .venv/bin/python -m nova.main
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import config, db, face, tools, voice
from .agent import Agent

agent = Agent()


@asynccontextmanager
async def lifespan(_app):
    import threading
    threading.Thread(target=voice.warmup, daemon=True, name="nova-warmup").start()
    problem = agent.health()
    logging.getLogger("nova").warning("BEYİN SORUNU: %s", problem) if problem else \
        logging.getLogger("nova").info("Beyin hazır: %s",
                                       config.CLAUDE_MODEL if agent.claude else f"{config.MODEL} (lokal)")
    if config.WAKE_ENABLED:
        from .wake import WakeListener
        WakeListener(agent).start()
    yield

app = FastAPI(title="NOVA", lifespan=lifespan)

BRIEFING_PROMPT = ("Bana kısa bir durum brifingi ver: açık görevlerim, bugünkü takvimim ve hangi projelere uzun "
                   "süredir dokunmadığım. Gerekli araçları kullan.")


def auth(authorization: str = Header(default=""), t: str = Query(default="")) -> None:
    """Telefon: Authorization başlığı. Yüz ekranı (tarayıcı): ?t=token."""
    if not config.TOKEN:
        raise HTTPException(500, "NOVA_TOKEN ayarlanmamış (.env)")
    ok = secrets.compare_digest(authorization, f"Bearer {config.TOKEN}") or secrets.compare_digest(t, config.TOKEN)
    if not ok:
        raise HTTPException(401, "Yetkisiz")


class ChatIn(BaseModel):
    text: str
    speak: bool = True


def _respond(text: str, speak: bool) -> dict:
    out = agent.ask(text)
    out["heard"] = text
    out["audio"] = voice.speak(out["reply"]) if speak else None
    out["pending_actions"] = [a for a in db.list_pending() if a["id"] in out.pop("pending")]
    return out


app.include_router(face.router(auth))


@app.get("/health")
def health():
    problem = agent.health()
    return {"ok": problem is None, "model": config.MODEL, "problem": problem}


@app.post("/chat", dependencies=[Depends(auth)])
def chat(body: ChatIn):
    return _respond(body.text, body.speak)


@app.post("/voice", dependencies=[Depends(auth)])
def voice_in(file: UploadFile = File(...)):
    suffix = Path(file.filename or "a.m4a").suffix or ".m4a"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
        f.write(file.file.read())
        tmp = Path(f.name)
    try:
        text = voice.transcribe(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    if not text:
        return {"heard": "", "reply": "Sizi duyamadım efendim.", "audio": voice.speak("Sizi duyamadım efendim."),
                "pending_actions": []}
    return _respond(text, True)


@app.post("/briefing", dependencies=[Depends(auth)])
def briefing():
    return _respond(BRIEFING_PROMPT, True)


@app.get("/audio/{name}", dependencies=[Depends(auth)])
def audio(name: str):
    p = (config.AUDIO_DIR / name).resolve()
    if p.parent != config.AUDIO_DIR.resolve() or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="audio/mp4")


@app.get("/actions", dependencies=[Depends(auth)])
def actions():
    return db.list_pending()


@app.post("/actions/{action_id}/{decision}", dependencies=[Depends(auth)])
def decide(action_id: int, decision: str):
    a = db.get_pending(action_id)
    if not a or a["status"] != "pending":
        raise HTTPException(404, "Bekleyen işlem yok")
    if decision == "reject":
        db.finish_pending(action_id, "rejected")
        msg = f"Peki efendim, iptal ettim: {a['summary']}."
        db.add_message("assistant", msg)
        return {"reply": msg, "result": None}
    if decision != "approve":
        raise HTTPException(400, "approve ya da reject")
    result = tools.execute(a["tool"], a["args"])
    db.finish_pending(action_id, "done", result)
    db.add_message("assistant", f"[Onaylanan işlem #{action_id} sonucu] {result[:1500]}")
    msg = f"Tamamlandı efendim: {a['summary']}."
    return {"reply": msg, "result": result, "audio": voice.speak(msg)}


@app.get("/tasks", dependencies=[Depends(auth)])
def tasks_list():
    return db.rows("SELECT * FROM tasks WHERE status='open' ORDER BY due IS NULL, due, id")


def run() -> None:
    import uvicorn
    from logging.handlers import RotatingFileHandler
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s",
                        handlers=[logging.StreamHandler(),      # terminal
                                  RotatingFileHandler(config.DATA_DIR / "nova.log", maxBytes=2_000_000,
                                                      backupCount=1, encoding="utf-8")])   # sorun ararken okunur
    uvicorn.run(app, host=config.HOST, port=config.PORT)


if __name__ == "__main__":
    run()
