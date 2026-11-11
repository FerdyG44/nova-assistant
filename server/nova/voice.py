"""Ses: Whisper (Apple GPU'da MLX) ile sesten yazıya; Edge (Emel) ile yazıdan sese, internet yoksa Piper, o da yoksa macOS `say`."""
from __future__ import annotations

import logging
import re
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path

from . import config

log = logging.getLogger("nova.voice")


@lru_cache(maxsize=2)
def _whisper(name: str):
    from faster_whisper import WhisperModel  # ilk kullanımda yüklenir
    return WhisperModel(name, device="cpu", compute_type="int8")


STT_PROMPT = (f"{config.NAME}, " + (config.STT_HINTS + ", " if config.STT_HINTS else "")
              + "Spotify, Safari, terminal, onaylıyorum, görev ekle, televizyonu aç.")


def _mlx_ok() -> bool:
    try:
        import mlx_whisper  # noqa: F401  Apple Silicon GPU'da çalışır
        return True
    except Exception:
        return False


def transcribe(source, model: str | None = None, prompt: str | None = None) -> str:
    """source: dosya yolu ya da 16 kHz mono float32 numpy dizisi.

    Komutlar için Apple GPU'da (MLX) large-v3-turbo kullanılır: hem çok daha doğru hem hızlı.
    İsim yakalama gibi hızlı işler için (model verilince) küçük faster-whisper modeli kullanılır.
    """
    src = str(source) if isinstance(source, Path) else source
    if model is None and config.STT_ENGINE == "mlx" and _mlx_ok():
        import mlx_whisper
        out = mlx_whisper.transcribe(src, path_or_hf_repo=config.MLX_WHISPER_MODEL, language="tr",
                                     initial_prompt=prompt or STT_PROMPT, condition_on_previous_text=False,
                                     temperature=0.0)
        return _clean_stt(out.get("text", ""))
    segments, _ = _whisper(model or config.WHISPER_MODEL).transcribe(
        src, language="tr", vad_filter=True, beam_size=1 if model else 5,
        initial_prompt=prompt or STT_PROMPT)
    return _clean_stt(" ".join(s.text.strip() for s in segments))


def _clean_stt(text: str) -> str:
    text = text.strip()
    # whisper sessizlikte bazen altyazı kalıpları uydurur
    for junk in ("Altyazı M.K.", "İzlediğiniz için teşekkürler", "Abone olmayı unutmayın"):
        if junk.lower() in text.lower() and len(text) < 60:
            return ""
    return text


# ---------------------------------------------------------------- Edge: Microsoft'un sinir ağı sesleri (ücretsiz, internet ister)
# Her dil kendi yerel sesiyle okunur: aksan doğru olur, rakamlar o dilde söylenir.
EDGE_VOICES = {"en": "en-US-AvaNeural", "sv": "sv-SE-SofieNeural", "pt": "pt-BR-FranciscaNeural",
               "es": "es-ES-ElviraNeural", "de": "de-DE-KatjaNeural", "fr": "fr-FR-DeniseNeural",
               "it": "it-IT-ElsaNeural", "nl": "nl-NL-ColetteNeural", "ru": "ru-RU-SvetlanaNeural",
               "ar": "ar-SA-ZariyahNeural", "az": "az-AZ-BanuNeural"}
LANGS = ["tr"] + list(EDGE_VOICES)
_lang_ready = False


def detect_lang(text: str, default: str = "tr") -> str:
    """Cümlenin dilini bulur. Çok kısa cümlelerde (Tamam., Evet.) önceki dil korunur."""
    global _lang_ready
    if len(re.sub(r"[^\w]", "", text)) < 12:
        return default
    try:
        import py3langid as langid
        if not _lang_ready:
            langid.set_languages(LANGS)
            _lang_ready = True
        return langid.classify(text)[0]
    except Exception:
        return default


def _edge_mp3(text: str, path: Path, lang: str = "tr") -> None:
    import edge_tts
    v = config.EDGE_VOICE if lang == "tr" else EDGE_VOICES.get(lang, config.EDGE_VOICE)
    rate = config.EDGE_RATE if lang == "tr" else "+0%"
    edge_tts.Communicate(text, v, rate=rate, pitch=config.EDGE_PITCH if lang == "tr" else "+0Hz").save_sync(str(path))
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("boş ses dosyası (internet bağlantısı?)")


# ---------------------------------------------------------------- Piper: lokal Türkçe ses (internet yokken yedek)
def _piper_model() -> Path:
    """Model ilk kullanımda indirilir (~60 MB) ve data/voices altında saklanır."""
    from piper.download_voices import download_voice
    d = config.DATA_DIR / "voices"
    d.mkdir(parents=True, exist_ok=True)
    for name in dict.fromkeys([config.PIPER_VOICE, "tr_TR-dfki-medium", "tr_TR-fettah-medium"]):
        onnx = d / f"{name}.onnx"
        if onnx.exists() and (d / f"{name}.onnx.json").exists():
            return onnx
        try:
            log.info("Piper ses modeli indiriliyor: %s", name)
            download_voice(name, d)
            return onnx
        except Exception as e:
            log.warning("%s indirilemedi: %s", name, e)
    raise RuntimeError("Hiçbir Piper Türkçe sesi indirilemedi")


@lru_cache(maxsize=1)
def _piper():
    from piper import PiperVoice
    return PiperVoice.load(_piper_model())


def _piper_wav(text: str, path: Path, lang: str = "tr") -> None:
    import wave
    from piper import SynthesisConfig
    with wave.open(str(path), "wb") as wf:
        _piper().synthesize_wav(text, wf, syn_config=SynthesisConfig(length_scale=config.PIPER_SPEED))


def _say_aiff(text: str, path: Path, lang: str = "tr") -> None:
    subprocess.run(["say", "-v", config.TTS_VOICE, "-o", str(path), text], check=True, timeout=60)


ENGINES = {"edge": (_edge_mp3, ".mp3"), "piper": (_piper_wav, ".wav"), "say": (_say_aiff, ".aiff")}


def speak(text: str, lang: str | None = None) -> str | None:
    """Metni m4a'ya çevirir, dosya adını döner. Seçili motor çalışmazsa sıradakini dener: edge → piper → say.
    lang verilmezse cümlenin dili otomatik bulunur."""
    lang = lang or detect_lang(text)
    name = uuid.uuid4().hex
    m4a = config.AUDIO_DIR / f"{name}.m4a"
    order = [config.TTS_ENGINE] + [e for e in ENGINES if e != config.TTS_ENGINE]
    for label in order:
        fn, ext = ENGINES[label]
        raw = config.AUDIO_DIR / f"{name}{ext}"
        try:
            fn(text, raw, lang)
            subprocess.run(["afconvert", "-f", "m4af", "-d", "aac", str(raw), str(m4a)], check=True, timeout=60)
            _cleanup()
            return m4a.name
        except Exception as e:  # bir motor çalışmazsa diğerini dene
            log.warning("%s ile ses üretilemedi: %s", label, e)
        finally:
            raw.unlink(missing_ok=True)
    return None


def warmup() -> None:
    """Modelleri açılışta indirip yükler; ilk cevap beklemesin."""
    jobs = [("yedek ses (Piper)", _piper)]
    if config.STT_ENGINE == "mlx" and _mlx_ok():
        import numpy as np
        jobs += [("kulak (whisper large-v3-turbo, GPU)", lambda: transcribe(np.zeros(16000, dtype=np.float32)))]
    else:
        jobs += [("kulak (whisper)", lambda: _whisper(config.WHISPER_MODEL))]
    if config.WAKE_ENABLED:
        jobs += [("isim algılama (whisper tiny)", lambda: _whisper(config.WAKE_MODEL))]
    for label, fn in jobs:
        try:
            fn()
            log.info("Hazır: %s", label)
        except Exception as e:
            log.warning("%s yüklenemedi: %s", label, e)


def chime() -> None:
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def _cleanup(keep: int = 50) -> None:
    files = sorted(config.AUDIO_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        f.unlink(missing_ok=True)
