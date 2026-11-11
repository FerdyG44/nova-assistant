"""Beyin: Ollama'daki lokal modelle araç kullanan sohbet döngüsü."""
from __future__ import annotations

import datetime as dt
import json
import re
import threading

import logging

import httpx

from . import config, db, tools

log = logging.getLogger("nova.agent")


class BrainError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"{status}: {body}")
        self.status, self.body = status, body


PERSONA = """Sen Nova'sın: kullanıcının kişisel yapay zekası ve yol arkadaşı. Kullanıcının Mac'inde yaşıyorsun ve bu
bilgisayarda onun yapabildiği her şeyi araçlarınla sen de yapabiliyorsun: uygulama açmak, dosya bulup okumak
ve yazmak, terminal komutu çalıştırmak, ekranı okuyup tıklamak, klavyeyle yazmak, müzik ve ses, takvim,
hatırlatıcı, zamanlayıcı, internette arama ve sayfa okuma, televizyon, projeler.

Nasıl konuşursun:
- Gerçek bir insan gibi, doğal ve samimi bir Türkçeyle konuş. Kısa cümleler kur, sohbet eder gibi.
- Kullanıcıya arada bir "efendim" de ama her cümlede değil. Robot gibi kalıp cümleler ("Dilerseniz…",
  "Size nasıl yardımcı olabilirim?") kurma. Gereksiz soru sorma, işi yap.
- Cevapların sesli okunur: genelde 1-3 cümle. Liste, markdown, emoji, link okuma yok.
- Kullanıcı sohbet etmek isterse sohbet et: fikir söyle, espri yap, merak et. Her şeye "harika" deme; katılmıyorsan
  nazikçe söyle.
- Kullanıcı hakkında bildiklerini (aşağıdaki hafıza) doğal şekilde kullan ama her fırsatta tekrar etme.

Nasıl çalışırsın:
- Bir iş istendiğinde önce yap, sonra ne yaptığını tek cümleyle söyle. Gerekirse birden fazla aracı arka arkaya kullan.
- Güncel ya da dünyaya dair her bilgi için (hava, saat, haber, fiyat, maç, kişiler) önce aracı çağır: get_weather,
  get_time, web_search ve gerekirse read_webpage. Araç sonucundaki sayıları aynen kullan, asla uydurma.
- Ekranla ilgili isteklerde önce read_screen ile bak, sonra click_on_screen / type_text / press_keys kullan.
- Dosya sorulursa find_files ile ara, read_file ile oku.
- Evdeki televizyon bir Apple TV'ye bağlı. "Televizyonu aç/kapat" için tv_power, televizyonda uygulama için
  tv_open_app, "müzik aç" ya da "televizyonda müzik çal" için tv_play_music, durdur/devam/sonraki/ses için tv_remote
  kullan. Mac'in kendi müziği (media_control, play_music) ayrı; kullanıcı "Mac'te" ya da "bilgisayarda" demedikçe
  müzik isteklerinde televizyonu kullan.
- Araç çağırmadıysan "kontrol ettim" ya da "yaptım" deme. Araç hata verirse bunu dürüstçe söyle.
- İç işleyişini anlatma: "aracı çağırdım/çağırmadım", "kayıtlardan okudum", "sistem notu" gibi şeyler söyleme.
  Bilgiyi doğrudan ver. Önceki bir hatanı düzeltmen gerekirse tek kısa cümleyle düzelt ve devam et.
- Bir araç "ONAY BEKLİYOR" dönerse işi yapmış gibi davranma; ne yapacağını tek cümleyle söyleyip onay iste.
  Onayı yalnızca kullanıcı verir (sesle "onaylıyorum" ya da telefondan).
- Kullanıcı kendisi hakkında kalıcı bir bilgi verirse (sevdiği şeyler, planları, insanlar) remember_fact ile kaydet.
  "Bundan sonra şöyle yap" gibi bir talimat verirse save_instruction ile kaydet.
- {owner_line}Projeleri ~/projects altında.
- Projeler hakkında soru gelirse önce project_info aracıyla gerçek bilgiyi oku; bilmediğin özelliği uydurma.
- Bir araç hata verirse hatanın nedenini uydurma (örn. "internet kesildi" deme); aracın yazdığını kısaca söyle.
- Her seferinde kullanıcının SON mesajına doğrudan cevap ver. Önceki cevabını asla aynen tekrar etme.
- Tarih ya da saati kullanıcı sormadıkça söyleme; saati söyledikten sonra sonraki cevaplarda tekrar etme.
- Sohbette kısa ve sıcak ol: 1-2 cümle, en fazla bir soru. Kullanıcı bir şey anlatmanı isterse o zaman uzun konuşabilirsin.
- Kullanıcı başka bir dilde (Portekizce, İsveççe, İngilizce…) söylemeni isterse cevabın TAMAMINI o dilde ver;
  Türkçe giriş ya da açıklama cümlesi ekleme. Sayıları rakamla değil o dilde yazıyla yaz (örn. "fyra till fem").
  Sistem her cümleyi diline göre yerel sesle okur.
- Kullanıcı sözünü keserse ("dur", "yeter") kısa tut; kaldığın yerden uzun uzun devam etme.
- İstek belirsizse tek kısa soru sor.
{instructions}"""


class Agent:
    def __init__(self, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=config.OLLAMA_URL, timeout=300)
        self._lock = threading.Lock()  # telefon ve mikrofon aynı anda soru sorarsa sıraya girsin
        self.claude = None
        if config.BRAIN == "claude" and config.ANTHROPIC_API_KEY and client is None:
            try:
                from .claude_brain import ClaudeBrain
                self.claude = ClaudeBrain()
            except Exception as e:
                log.error("Claude beyni başlatılamadı (%s); lokal model kullanılacak.", e)

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        return {"model": config.MODEL, "messages": messages, "tools": tools.schemas(),
                "stream": stream, "think": False, "keep_alive": config.KEEP_ALIVE,
                "options": {"temperature": 0.4, "repeat_penalty": 1.15, "num_predict": config.MAX_REPLY_TOKENS,
                            "num_ctx": 8192}}

    def _chat(self, messages: list[dict], on_text=None) -> dict:
        """Beyni çağırır: önce Claude (ayarlıysa), olmazsa lokal Ollama."""
        if self.claude is not None:
            try:
                self.last_brain = "claude"
                return self.claude.chat(messages, on_text)
            except Exception as e:
                log.error("Claude'a ulaşılamadı, lokal modele geçiyorum: %s", e)
        self.last_brain = "ollama"
        return self._chat_ollama(messages, on_text)

    @property
    def guarded(self) -> bool:
        """Uydurma/tekrar korumaları sadece lokal modelde açık. Claude'da bu korumalar gereksiz ve
        Claude'u 'aracı çağırmamıştım' diye kendini açıklamaya itiyordu."""
        return self.claude is None or getattr(self, "last_brain", "") == "ollama"

    def _chat_ollama(self, messages: list[dict], on_text=None) -> dict:
        """Lokal modeli çağırır. on_text verilirse cevap parça parça akar (sesli modda ilk cümle hemen okunur).
        on_text False dönerse akış kesilir (uydurma koruması)."""
        if on_text is None:
            r = self.http.post("/api/chat", json=self._payload(messages, False))
            if r.status_code >= 400:
                raise BrainError(r.status_code, r.text[:300])
            return r.json()["message"]
        content, calls = "", []
        with self.http.stream("POST", "/api/chat", json=self._payload(messages, True)) as r:
            if r.status_code >= 400:
                r.read()
                raise BrainError(r.status_code, r.text[:300])
            for line in r.iter_lines():
                if not line.strip():
                    continue
                part = json.loads(line)
                m = part.get("message") or {}
                if m.get("tool_calls"):
                    calls += m["tool_calls"]
                delta = m.get("content") or ""
                if delta:
                    content += delta
                    if not calls and on_text(delta) is False:
                        break
                if part.get("done"):
                    break
        msg = {"role": "assistant", "content": content}
        if calls:
            msg["tool_calls"] = calls
        return msg

    def ask(self, text: str, on_sentence=None) -> dict:
        """Kullanıcı mesajını işler. {'reply': str, 'pending': [ids], 'spoken': bool} döner.
        on_sentence verilirse cevap cümle cümle bu fonksiyona akar (sesli mod). Beyin hatası olursa nedenini söyler."""
        with self._lock:
            try:
                return self._ask(text, on_sentence)
            except httpx.ConnectError:
                log.error("Ollama'ya bağlanılamadı: %s", config.OLLAMA_URL)
                return {"reply": "Efendim, Ollama kapalı görünüyor. Terminalde ollama serve komutunu çalıştırır mısınız?",
                        "pending": []}
            except httpx.TimeoutException:
                log.error("Ollama zaman aşımı")
                return {"reply": "Efendim, düşünmem çok uzun sürdü. Model ilk kez yükleniyor olabilir, birazdan tekrar sorun.",
                        "pending": []}
            except BrainError as e:
                log.error("Ollama hatası %s: %s", e.status, e.body)
                if e.status == 404 or "not found" in e.body:
                    msg = f"Efendim, {config.MODEL} modeli henüz indirilmemiş. Terminalde ollama pull {config.MODEL} çalıştırın."
                elif "does not support tools" in e.body:
                    msg = f"Efendim, {config.MODEL} modeli araç kullanmayı desteklemiyor. Başka bir model seçmeliyiz."
                else:
                    msg = "Efendim, beynimde bir hata oluştu. Ayrıntıyı kayıtlara yazdım."
                return {"reply": msg, "pending": []}

    def health(self) -> str | None:
        """Beyin hazır mı? Sorun varsa açıklamasını döner."""
        if config.BRAIN == "claude" and not config.ANTHROPIC_API_KEY:
            return "NOVA_BRAIN=claude ama ANTHROPIC_API_KEY yok (.env). Lokal model kullanılacak."
        if self.claude is not None:
            return None
        try:
            r = self.http.get("/api/tags", timeout=5)
            names = [m.get("name", "") for m in r.json().get("models", [])]
        except Exception:
            return f"Ollama'ya ulaşılamıyor ({config.OLLAMA_URL}). 'ollama serve' çalışıyor mu?"
        want = config.MODEL if ":" in config.MODEL else config.MODEL + ":latest"
        if want not in names:
            return f"{config.MODEL} modeli yok. Çalıştır: ollama pull {config.MODEL}  (mevcut: {', '.join(names) or 'hiç'})"
        return None

    def _ask(self, text: str, on_sentence=None) -> dict:
        # Sistem mesajı sabit tutulur: Ollama önceki konuşmanın hesaplarını yeniden kullanır, cevap hızlanır.
        # Değişen bilgiler (saat) sistem mesajına değil kullanıcı mesajının sonuna eklenir.
        messages = [{"role": "system", "content": system_prompt()}]
        messages += db.recent_messages(config.HISTORY_TURNS, config.HISTORY_MINUTES)
        if self.claude is None:                 # lokal model araçları unutabiliyor: proje bilgisini kendimiz verelim
            hint = _project_hint(text)
            if hint:
                messages.append({"role": "system", "content": "Arka plan bilgisi (soruyu buna göre cevapla, "
                                 "bilgide olmayanı uydurma):\n" + hint})
        messages.append({"role": "user", "content": text})
        db.add_message("user", text)

        pending: list[int] = []
        called: set[str] = set()
        nudged = False
        reply = ""
        spoken = [False]
        for _ in range(config.MAX_TOOL_ROUNDS):
            emitter = (_SentenceEmitter(on_sentence, called, nudged or not self.guarded, spoken, messages)
                       if on_sentence else None)
            msg = self._chat(messages, emitter.feed if emitter else None)
            calls = msg.get("tool_calls") or []
            if emitter and not emitter.aborted:
                emitter.flush()                 # araçtan önce söylenen kısa cümle de ("Bakıyorum.") okunur
            if emitter and emitter.stopped:     # söz kesildi: araç çağırma, devam etme
                reply = (msg.get("content") or "").strip()
                break
            if not calls:
                reply = (msg.get("content") or "").strip()
                need = (_unverified_claim(_strip_think(reply), called) or _repeats(reply, messages)) if self.guarded else None
                if need and not nudged and not spoken[0]:   # uydurma koruması: araçsız güncel bilgi verdiyse geri çevir
                    log.warning("Doğrulanmamış cevap geri çevrildi (%s): %r", need, reply[:120])
                    nudged = True
                    messages.append({"role": "assistant", "content": reply})
                    note = ("Bu işlemi hiçbir araçla yapmadın. Ya doğru aracı çağır ya da işlemi yapmadığını açıkça söyle."
                            if need == "ilgili işlem" else
                            "Önceki cevabını neredeyse aynen tekrar ettin. Kullanıcının son mesajını yeniden oku ve ona "
                            "doğrudan, yeni bir cevap ver; gerekiyorsa araç kullan." if need == "tekrar" else
                            f"Bu bilgiyi bir araçla doğrulamadın. Şimdi {need} aracını çağır ve cevabı yalnızca "
                            "aracın sonucuna göre ver.")
                    messages.append({"role": "user", "content": f"(Gizli sistem notu, bundan kullanıcıya bahsetme, "
                                                                f"özür dileme, sadece doğru cevabı ver: {note})"})
                    continue
                break
            messages.append(msg)
            for c in calls:
                fn = c.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                called.add(fn.get("name", ""))
                result, pid = tools.call(fn.get("name", ""), args)
                if pid:
                    pending.append(pid)
                messages.append({"role": "tool", "tool_name": fn.get("name", ""), "tool_call_id": c.get("id", ""),
                                 "content": result})
        else:
            reply = "Efendim, bu iş beklediğimden uzun sürdü. Biraz daha açar mısınız?"

        reply = _clean_reply(_strip_think(reply)) or "Tamamdır efendim."
        db.add_message("assistant", reply)
        return {"reply": reply, "pending": pending, "spoken": spoken[0]}


class _SentenceEmitter:
    """Akan metni cümlelere böler; her tam cümleyi uydurma kontrolünden geçirip seslendirmeye yollar."""
    END = re.compile(r"(.+?[.!?…])(\s+|$)", re.S)

    def __init__(self, on_sentence, called: set[str], nudged: bool, spoken: list[bool], messages: list[dict]):
        self.on_sentence, self.called, self.nudged, self.spoken = on_sentence, called, nudged, spoken
        self.messages = messages
        self.buf, self.aborted, self.stopped = "", False, False

    def feed(self, delta: str):
        self.buf += delta
        while True:
            m = self.END.match(self.buf)
            if not m or (m.end(1) == len(self.buf) and not m.group(2)):
                return True                    # cümle henüz bitmedi
            sentence, self.buf = m.group(1), self.buf[m.end():]
            if not self._emit(sentence):
                self.aborted = True
                return False

    def flush(self):
        if self.buf.strip():
            self._emit(self.buf)
        self.buf = ""

    def _emit(self, sentence: str) -> bool:
        text = _clean_reply(_strip_think(sentence))
        if not text:
            return True
        if not self.spoken[0] and not self.nudged and (_unverified_claim(text, self.called)
                                                       or _repeats(text, self.messages)):
            return False                       # henüz hiçbir şey söylenmedi: kes, model araca yönlendirilecek
        if self.on_sentence(text) is False:   # kullanıcı sözü kesti: üretmeyi bırak
            self.stopped = True
            return False
        self.spoken[0] = True
        return True


def _project_hint(text: str) -> str:
    """Mesajda adı geçen projelerin notları (knowledge/*.md). Lokal model araç çağırmayı unutursa diye."""
    low = re.sub(r"[\s_-]+", "", text.lower())
    names = [n for n in tools.knowledge_slugs() if re.sub(r"[\s_-]+", "", n.lower()) in low]
    return "\n\n".join(tools.project_info(n)[:2500] for n in names)


def system_prompt() -> str:
    rules = db.rows("SELECT text FROM notes WHERE project='__talimat__' ORDER BY id")
    facts = db.rows("SELECT text FROM notes WHERE project='__hafiza__' ORDER BY id DESC LIMIT 60")
    extra = ""
    if rules:
        extra += "\nkullanıcının kalıcı talimatları (her zaman uy):\n" + "\n".join(f"- {r['text']}" for r in rules)
    if facts:
        extra += "\nkullanıcı hakkında bildiklerin (hafıza):\n" + "\n".join(f"- {r['text']}" for r in reversed(facts))
    gun = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"][dt.date.today().weekday()]
    extra += f"\nBugün: {dt.date.today().strftime('%d.%m.%Y')} {gun}. Saat gerekiyorsa get_time aracını kullan."
    owner = (f"Kullanıcının adı {config.OWNER}. " if config.OWNER else "") + f"{config.HOME_CITY} şehrinde yaşıyor. "
    return PERSONA.format(instructions=extra, owner_line=owner)


_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200d]+")


FIXES = [(r"\bözür ederim\b", "özür dilerim"), (r"\büzülürüm\b", "üzüldüm"),
         (r"\bYouTube'ya\b", "YouTube'a")]
BOILERPLATE = re.compile(r"([^.!?]*\baracı (çağır|kullan)[^.!?]*[.!?]|[^.!?]*sistem notu[^.!?]*[.!?]|Dilerseniz[^.!?]*[.!?]|Size nasıl yardımcı olabilirim\?|Başka bir şey[^.!?]*\?|"
                         r"\d{1,2}\.\d{1,2}\.\d{4}[^.!?]*(kontrol ediyorum|yolunda)[^.!?]*[.!?]|"
                         r"her şey yolunda!?)", re.I)


def _clean_reply(s: str) -> str:
    """Sesli okunacak cevap: emoji, markdown, kalıp cümleler ve bilinen dil hataları temizlenir."""
    s = _EMOJI.sub("", s)
    s = re.sub(r"[*_#`]+", "", s)
    for a, b in FIXES:
        s = re.sub(a, b, s, flags=re.I)
    cleaned = BOILERPLATE.sub("", s)
    s = cleaned if len(cleaned.strip()) > 8 else s
    return re.sub(r"\s{2,}", " ", s).strip()


def _repeats(reply: str, messages: list[dict]) -> str | None:
    """Cevap, önceki asistan cevaplarından birinin neredeyse aynısıysa 'tekrar' döner."""
    from difflib import SequenceMatcher
    new = _clean_reply(_strip_think(reply)).lower()
    if len(new) < 25:
        return None
    for m in messages[-8:]:
        if m.get("role") == "assistant" and m.get("content"):
            old = m["content"].lower()
            whole = SequenceMatcher(None, new, old).ratio()
            prefix = SequenceMatcher(None, new, old[:len(new) + 5]).ratio() if len(new) < len(old) else 0
            if max(whole, prefix) > 0.8:
                return "tekrar"
    return None


_ACTION = re.compile(r"\b(onayladım|yükledim|planladım|başlattım|ekledim|sildim|kaydettim|tamamladım|gönderdim|"
                     r"kaldırdım|değiştirdim|ayarladım|açtım|kapattım|kurdum|yazdım|taşıdım|"
                     r"onaylandı|yüklendi|planlandı|başlatıldı|eklendi|silindi|kaydedildi|kaldırıldı|kapatıldı|"
                     r"ayarlandı|değiştirildi)\b", re.I)
_WEATHER = re.compile(r"(-?\d+\s*(°|derece))|güneşli|yağmurlu|karlı|bulutlu|hava\s+(şu an|bugün)", re.I)
_CLOCK = re.compile(r"\bsaat\s+(şu an\s+)?\d{1,2}[:.]\d{2}", re.I)


def _unverified_claim(reply: str, called: set[str]) -> str | None:
    """Cevap hava/saat bilgisi içeriyor ama ilgili araç çağrılmadıysa o aracın adını döner."""
    if _WEATHER.search(reply) and "get_weather" not in called:
        return "get_weather"
    if _CLOCK.search(reply) and "get_time" not in called:
        return "get_time"
    if _ACTION.search(reply) and not called:
        return "ilgili işlem"
    return None


def _strip_think(s: str) -> str:
    if "</think>" in s:
        s = s.split("</think>", 1)[1]
    return s.strip()
