"""Mac mikrofonunu dinler. "Nova" duyunca yüzü tam ekran açar, komutu dinler, cevabı yüzle birlikte söyler.

Akış:  bekle → (isim duyuldu) yüz açılır + "tink" → komut dinlenir → düşünür → konuşur (dudaklar oynar)
       → 8 sn boyunca isim söylemeden devam edebilirsin → sessizlik olursa yüz kapanır.
"""
from __future__ import annotations

import logging
import queue
import re
import threading
import time

import numpy as np

from . import config, db, tools, voice
from .face import bus, close_face, open_face

log = logging.getLogger("nova.wake")
SR = 16000
BLOCK = 1600                       # 0.1 sn
GOODBYES = ("teşekkür", "sağ ol", "sağol", "görüşürüz", "çıkış", "tamam bu kadar", "hoşça kal")
SELF_OFF = {"kapat", "kapan", "uyu", "sus"}          # tek başına söylenirse Nova'nın kendisi kapanır
FILLER = {"nova", "tamam", "hadi", "artık", "lütfen", "şimdi", "sen", "kendini", "yüzü", "yüzünü", "efendim",
          "ya", "peki", "o", "zaman", "yap", "ederim", "ederiz", "çok", "bu", "kadar", "yeter"}
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?")


# ---------------------------------------------------------------- konuşma parçalarını ayırma
class Segmenter:
    """0.1 sn'lik ses bloklarından konuşma parçası çıkarır (enerji tabanlı, bağımlılıksız)."""

    def __init__(self, threshold: float, end_silence: float, max_len: float, preroll: float = .3):
        self.th, self.end_sil, self.max_len = threshold, end_silence, max_len
        self.pre_n = int(preroll * 10)
        self.reset()

    def reset(self):
        self.pre: list[np.ndarray] = []
        self.buf: list[np.ndarray] = []
        self.speaking = False
        self.silent = 0
        self.loud = 0

    def feed(self, block: np.ndarray) -> np.ndarray | None:
        rms = float(np.sqrt(np.mean(block ** 2)))
        if not self.speaking:
            self.pre = (self.pre + [block])[-self.pre_n:]
            self.loud = self.loud + 1 if rms > self.th else 0
            if self.loud >= 2:                       # 0.2 sn üst üste ses → konuşma başladı
                self.speaking, self.buf, self.silent = True, list(self.pre), 0
            return None
        self.buf.append(block)
        self.silent = self.silent + 1 if rms < self.th * .7 else 0
        if self.silent >= self.end_sil * 10 or len(self.buf) >= self.max_len * 10:
            out = np.concatenate(self.buf)
            self.reset()
            return out
        return None


def find_wake(text: str) -> tuple[bool, str]:
    """Metinde isim var mı? Varsa isimden sonra gelen kısmı (komut) da döner."""
    low = text.lower()
    for w in config.WAKE_WORDS:
        m = re.search(rf"\b{re.escape(w)}\b", low)
        if m:
            rest = text[m.end():].strip(" ,.!?;:'’")
            return True, rest
    return False, ""


def is_goodbye(text: str) -> bool:
    low = text.lower().strip(" .!?")
    if len(low.split()) > 8:
        return False
    # "kapat / kapan / sus": yalnızca neyin kapatılacağı söylenmediyse Nova'ya vedadır.
    # "Televizyonu kapat", "müziği kapat", "Safari'yi kapat" birer komuttur ve beyne gider.
    last = [w for w in _WORD.findall(re.split(r"[,.;!?]|\bve\b", low)[-1]) if w not in FILLER]
    if last and all(w in SELF_OFF for w in last):
        return True
    # "teşekkürler", "görüşürüz"…: yanında başka bir istek yoksa vedadır ("TV'yi kapat, teşekkürler" komuttur).
    rest, hit = low, False
    for g in GOODBYES:
        rest, n = re.subn(rf"\w*{re.escape(g)}\w*", " ", rest)
        hit = hit or n > 0
    return hit and len([w for w in _WORD.findall(rest) if w not in FILLER]) <= 1


APPROVE = re.compile(r"\b(onaylıyorum|onayla|onaylıyom|evet|olur|yap|başlat|tamam|kabul)", re.I)
REJECT = re.compile(r"\b(hayır|iptal|vazgeç|yapma|dur|istemiyorum|reddet)", re.I)


def approval_decision(text: str) -> bool | None:
    """Onay bekleyen bir işlem varken söylenen kısa cevap: True=onay, False=red, None=başka bir şey."""
    if len(text.split()) > 8:
        return None
    if REJECT.search(text):
        return False
    if APPROVE.search(text):
        return True
    return None


# ---------------------------------------------------------------- mikrofon
class Mic:
    def __init__(self):
        import sounddevice as sd  # sadece Mac'te gerekli
        self.q: queue.Queue = queue.Queue()
        self.stream = sd.InputStream(samplerate=SR, channels=1, dtype="float32", blocksize=BLOCK,
                                     callback=lambda d, *_: self.q.put(d[:, 0].copy()))
        self.stream.start()

    def flush(self):
        while not self.q.empty():
            self.q.get_nowait()

    def segment(self, end_silence: float, max_len: float, start_timeout: float | None = None,
                stop: threading.Event | None = None, threshold: float | None = None) -> np.ndarray | None:
        seg = Segmenter(threshold or config.MIC_THRESHOLD, end_silence, max_len)
        t0 = time.time()
        while True:
            if stop is not None and stop.is_set():
                return None
            try:
                out = seg.feed(self.q.get(timeout=0.3))
            except queue.Empty:
                out = None
            if out is not None:
                return out
            if start_timeout and not seg.speaking and time.time() - t0 > start_timeout:
                return None


# ---------------------------------------------------------------- akıcı konuşma
class Speaker:
    """İki aşamalı boru hattı: bir iş parçacığı sıradaki cümlenin sesini üretirken diğeri öncekini çalar.
    interrupt() ile her şey anında susturulur (Kullanıcı "dur" dediğinde)."""

    def __init__(self):
        self.texts: queue.Queue = queue.Queue()
        self.audios: queue.Queue = queue.Queue()
        self.interrupted = False
        self.current = ""                         # şu an okunan cümle (söz kesme kontrolünde kullanılır)
        self.recent: list[str] = []               # son okunan cümleler: mikrofon bunları gecikmeyle duyar
        self.lang = "tr"
        self.t1 = threading.Thread(target=self._synth, daemon=True)
        self.t2 = threading.Thread(target=self._play, daemon=True)
        self.t1.start()
        self.t2.start()

    def say(self, text: str) -> bool:
        """Cümleyi sıraya koyar. Söz kesildiyse False döner (model de üretmeyi bıraksın)."""
        if self.interrupted:
            return False
        if text and text.strip():
            self.texts.put(text.strip())
        return True

    def interrupt(self):
        self.interrupted = True
        for q in (self.texts, self.audios):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        bus.publish(type="stop")
        bus.ended.set()

    def finish(self, timeout: float = 180):
        self.texts.put(None)
        self.t1.join(timeout)
        self.t2.join(timeout)

    def _synth(self):
        while (text := self.texts.get()) is not None:
            if self.interrupted:
                continue
            self.lang = voice.detect_lang(text, default=self.lang)
            self.audios.put((voice.speak(text, self.lang), text))
        self.audios.put(None)

    def _play(self):
        item = self.audios.get()
        while item is not None:
            try:                                   # sonraki cümle hazırsa yüz "konuşuyor" durumunda kalsın
                nxt, got = self.audios.get(timeout=0.05), True
            except queue.Empty:
                nxt, got = None, False
            if not self.interrupted:
                self.current = item[1]
                self.recent = (self.recent + [item[1]])[-3:]
                bus.speak(item[0], item[1], more=got and nxt is not None)
            item = nxt if got else self.audios.get()
        self.current = ""


STOP_WORDS = re.compile(r"\b(dur|durr|sus|yeter|kes|stop|bekle|tamam tamam|nova)\b", re.I)


def is_stop_command(text: str) -> bool:
    """Gerçek bir söz kesme: en çok dört kelimelik kısa bir cümle ve içinde durdurma kelimesi var."""
    return len(text.split()) <= 4 and bool(STOP_WORDS.search(text))


class BargeIn(threading.Thread):
    """Nova konuşurken mikrofonu dinler; 'dur', 'sus', 'yeter' ya da 'Nova' duyarsa sözü keser.
    Hoparlörden gelen Nova'nın kendi sesi yanlışlıkla durdurmasın diye: o an okunan cümlede geçen kelime sayılmaz."""

    def __init__(self, mic: "Mic", speaker: Speaker):
        super().__init__(daemon=True, name="nova-bargein")
        self.mic, self.speaker, self.done = mic, speaker, threading.Event()

    def run(self):
        if not config.BARGE_IN:                 # NOVA_BARGE_IN=0: Nova sözünü hiç kesmeden bitirir
            return
        self.mic.flush()
        while not self.done.is_set() and not self.speaker.interrupted:
            audio = self.mic.segment(end_silence=0.35, max_len=3, start_timeout=0.6, stop=self.done,
                                     threshold=config.MIC_THRESHOLD * config.BARGE_IN_FACTOR)
            if audio is None or self.done.is_set():
                continue
            try:
                text = voice.transcribe(audio, model=config.WAKE_MODEL, prompt="dur, sus, yeter, Nova.")
            except Exception:
                continue
            m = STOP_WORDS.search(text)
            if not m:
                continue
            # 1) Nova'nın az önce söylediği bir kelimeyse kendi sesidir (mikrofon gecikmeyle duyar).
            said = " ".join(self.speaker.recent + [self.speaker.current])
            if re.search(rf"\b{re.escape(m.group(1))}\b", said, re.I):
                continue
            # 2) Küçük model ipucundaki kelimeleri ("dur", "Nova") olmayan yerde de duyabiliyor; özellikle Nova
            #    başka dilde konuşurken. Büyük modelle doğrula: gerçek bir "dur" kısa ve nettir.
            try:
                sure = voice.transcribe(audio, prompt="Günlük konuşma.")
            except Exception:
                continue
            if not is_stop_command(sure):
                log.info("Söz kesme sayılmadı (küçük model: %r, doğrulama: %r)", text, sure)
                continue
            log.info("Söz kesildi: %r", sure)
            self.speaker.interrupt()
            return

    def stop(self):
        self.done.set()
        self.join(2)


# ---------------------------------------------------------------- ana döngü
class WakeListener(threading.Thread):
    def __init__(self, agent):
        super().__init__(daemon=True, name="nova-wake")
        self.agent = agent

    def run(self):
        try:
            mic = Mic()
        except Exception as e:  # mikrofon izni yoksa sunucu yine de çalışsın
            log.error("Mikrofon açılamadı (%s). Sistem Ayarları → Gizlilik → Mikrofon iznini kontrol et.", e)
            return
        log.info("'%s' diye seslenilmesini bekliyorum…", config.NAME)
        while True:
            try:
                self._wait_for_name(mic)
            except Exception:
                log.exception("Dinleyici hatası")
                time.sleep(1)

    def _wait_for_name(self, mic: Mic):
        audio = mic.segment(end_silence=.7, max_len=8)
        if audio is None or len(audio) < SR * .3:
            return
        heard = voice.transcribe(audio, model=config.WAKE_MODEL)
        ok, rest = find_wake(heard)
        if not ok:
            return
        log.info("Uyandım: %r", heard)
        open_face()
        if len(rest.split()) >= 2:            # "Nova, projelerim ne durumda?" tek nefeste
            _, command = find_wake(voice.transcribe(audio))   # büyük modelle daha doğru metin
            command = command or rest
        else:
            voice.chime()
            bus.state("listening")
            bus.publish(type="heard", text="")
            command = self._listen(mic, start_timeout=6)
            if not command:
                self._say("Buradayım efendim, bir şey lazım olursa seslenin.")
                command = None
        self._conversation(mic, command)
        bus.state("idle")
        time.sleep(1.5)
        close_face()

    def _listen(self, mic: Mic, start_timeout: float) -> str | None:
        mic.flush()
        audio = mic.segment(end_silence=config.END_SILENCE, max_len=30, start_timeout=start_timeout)
        if audio is None:
            return None
        bus.state("thinking")
        text = voice.transcribe(audio)
        return text or None

    def _decide(self, pending: list[int], approve: bool) -> str:
        done = []
        for pid in pending:
            a = db.get_pending(pid)
            if not a or a["status"] != "pending":
                continue
            if not approve:
                db.finish_pending(pid, "rejected")
                done.append(f"iptal edildi: {a['summary']}")
                continue
            bus.state("thinking")
            result = tools.execute(a["tool"], a["args"])
            db.finish_pending(pid, "done", result)
            db.add_message("assistant", f"[Onaylanan işlem #{pid} sonucu] {result[:1500]}")
            ok = not result.lower().startswith(("hata", "error", "izin yok")) and "error:" not in result.lower()
            done.append(f"{'tamamlandı' if ok else 'hata verdi'}: {a['summary']}")
            log.info("Sesli onayla çalıştırıldı #%s → %s", pid, result[:300])
        msg = "Peki efendim. " if not approve else "Efendim, "
        return msg + "; ".join(done) + "." if done else "Bekleyen bir işlem kalmamış efendim."

    def _say(self, text: str):
        bus.speak(voice.speak(text), text)

    def _conversation(self, mic: Mic, command: str | None):
        pending: list[int] = []                 # bu sohbette onay bekleyen işlemler
        while command:
            bus.publish(type="heard", text=command)
            if is_goodbye(command):
                self._say("Rica ederim efendim.")
                return
            pending = [p for p in pending if (db.get_pending(p) or {}).get("status") == "pending"]
            decision = approval_decision(command) if pending else None
            if decision is not None:            # sesli onay: modele bırakılmaz, doğrudan işlenir
                reply = self._decide(pending, decision)
                pending = []
            else:
                bus.state("thinking")
                speaker = Speaker()               # cümleler hazır oldukça seslendirilir
                barge = BargeIn(mic, speaker)     # konuşurken "dur" denirse susar
                barge.start()
                try:
                    out = self.agent.ask(command, on_sentence=speaker.say)
                    reply, new, spoken = out["reply"], out.get("pending", []), out.get("spoken", False)
                except Exception:
                    log.exception("Ajan hatası")
                    reply, new, spoken = "Efendim, beynime ulaşamadım. Ollama çalışıyor mu?", [], False
                if not spoken:
                    speaker.say(reply)
                if new:
                    pending = new
                    if "?" not in reply[-40:]:
                        speaker.say("Onaylıyor musunuz efendim?")
                speaker.finish()
                barge.stop()
                reply = None
            if reply:
                self._say(reply)
            bus.state("listening")
            command = self._listen(mic, start_timeout=config.FOLLOWUP_SECONDS)
