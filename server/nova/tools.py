"""Nova'nın elleri. Her araç: açıklama + parametre şeması + fonksiyon + onay gerekip gerekmediği.

Kural: okuma ve normal işler hemen yapılır; silme, gönderme, yayınlama, sistem ayarı gibi riskli işler onay ister.
Onay isteyen bir araç çağrıldığında hemen çalışmaz, pending_actions tablosuna yazılır ve
telefonda "Onayla / Reddet" butonu çıkar.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import config, db


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, Any]
    fn: Callable[..., str]
    required: list[str] = field(default_factory=list)
    confirm: Callable[[dict], bool] = lambda args: False
    summary: Callable[[dict], str] = lambda args: ""

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.params, "required": self.required}}}


TOOLS: dict[str, Tool] = {}


def tool(name, description, params=None, required=None, confirm=None, summary=None):
    def deco(fn):
        TOOLS[name] = Tool(name, description, params or {}, fn, required or [],
                           confirm or (lambda a: False), summary or (lambda a: name))
        return fn
    return deco


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120) -> str:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return f"Komut bulunamadı: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return f"Zaman aşımı ({timeout} sn)"
    out = (p.stdout + ("\n" + p.stderr if p.stderr.strip() else "")).strip()
    return (out[:6000] + "\n…(kısaltıldı)") if len(out) > 6000 else (out or f"(çıktı yok, kod {p.returncode})")


# ---------------------------------------------------------------- görevler & notlar
@tool("add_task", "Yeni görev ekle.",
      {"title": {"type": "string"}, "project": {"type": "string"},
       "due": {"type": "string", "description": "YYYY-MM-DD"}}, ["title"])
def add_task(title: str, project: str = "", due: str = "") -> str:
    tid = db.execute("INSERT INTO tasks(title, project, due, created) VALUES (?,?,?,?)",
                     title, project or None, due or None, time.time())
    return f"Görev #{tid} eklendi: {title}"


@tool("list_tasks", "Açık görevleri listele (isteğe bağlı proje filtresi).",
      {"project": {"type": "string"}, "include_done": {"type": "boolean"}})
def list_tasks(project: str = "", include_done: bool = False) -> str:
    sql = "SELECT id, title, project, due, status FROM tasks WHERE 1=1"
    params: list = []
    if not include_done:
        sql += " AND status='open'"
    if project:
        sql += " AND project LIKE ?"
        params.append(f"%{project}%")
    r = db.rows(sql + " ORDER BY due IS NULL, due, id", *params)
    if not r:
        return "Açık görev yok."
    return "\n".join(f"#{t['id']} [{t['project'] or '-'}] {t['title']}"
                     + (f" (son: {t['due']})" if t['due'] else "")
                     + ("" if t['status'] == 'open' else " ✓") for t in r)


@tool("complete_task", "Görevi tamamlandı olarak işaretle.", {"id": {"type": "integer"}}, ["id"])
def complete_task(id: int) -> str:
    db.execute("UPDATE tasks SET status='done', done_at=? WHERE id=?", time.time(), id)
    return f"Görev #{id} tamamlandı."


@tool("add_note", "Not kaydet.", {"text": {"type": "string"}, "project": {"type": "string"}}, ["text"])
def add_note(text: str, project: str = "") -> str:
    nid = db.execute("INSERT INTO notes(text, project, created) VALUES (?,?,?)", text, project or None, time.time())
    return f"Not #{nid} kaydedildi."


@tool("save_instruction", "kullanıcının kalıcı bir talimatını kaydet (örn. 'emoji kullanma', 'cevapları kısa tut').",
      {"text": {"type": "string"}}, ["text"])
def save_instruction(text: str) -> str:
    db.execute("INSERT INTO notes(text, project, created) VALUES (?,?,?)", text, "__talimat__", time.time())
    return f"Kalıcı talimat kaydedildi: {text}"


@tool("remember_fact", "kullanıcı hakkında kalıcı bir bilgiyi hafızaya kaydet (sevdikleri, planları, tanıdıkları…).",
      {"fact": {"type": "string"}}, ["fact"])
def remember_fact(fact: str) -> str:
    db.execute("INSERT INTO notes(text, project, created) VALUES (?,?,?)", fact, "__hafiza__", time.time())
    return f"Hafızaya kaydedildi: {fact}"


@tool("forget_fact", "Hafızadaki bir bilgiyi ya da talimatı sil (içinde geçen kelimeyle).",
      {"query": {"type": "string"}}, ["query"])
def forget_fact(query: str) -> str:
    rows = db.rows("SELECT id, text FROM notes WHERE project IN ('__hafiza__','__talimat__') AND text LIKE ?", f"%{query}%")
    for r in rows:
        db.execute("DELETE FROM notes WHERE id=?", r["id"])
    return f"{len(rows)} kayıt silindi: " + "; ".join(r["text"] for r in rows) if rows else "Eşleşen kayıt yok."


@tool("search_notes", "Notlarda ara.", {"query": {"type": "string"}}, ["query"])
def search_notes(query: str) -> str:
    r = db.rows("SELECT id, text, project, created FROM notes WHERE text LIKE ? OR project LIKE ? "
                "ORDER BY id DESC LIMIT 20", f"%{query}%", f"%{query}%")
    return "\n".join(f"#{n['id']} [{n['project'] or '-'}] {n['text']}" for n in r) or "Not bulunamadı."


# ---------------------------------------------------------------- dosya erişimi ve risk sınıflandırma
PROTECTED = (".ssh", ".gnupg", "Library/Keychains", ".aws", ".config/gcloud", "Library/Cookies",
             "Library/Application Support/Google/Chrome", ".netrc", ".env", ".pyatv.conf")


def _home_path(path: str | Path, base: Path | None = None) -> Path:
    """Ev klasörü içinde bir yol. Gizli anahtar/şifre klasörleri yasak."""
    raw = Path(str(path)).expanduser()
    p = (raw if raw.is_absolute() else (base or config.HOME_DIR) / raw).resolve()
    home = config.HOME_DIR.resolve()
    if p != home and home not in p.parents:
        raise PermissionError(f"Sadece ev klasörün ({home}) içinde çalışabilirim.")
    rel = str(p.relative_to(home)) if p != home else ""
    if any(rel == x or rel.startswith(x + "/") or ("/" + x) in ("/" + rel) for x in PROTECTED):
        raise PermissionError("Bu klasör şifre/anahtar içerdiği için yasak.")
    return p


_inside_projects = lambda path: _home_path(path, config.PROJECTS_DIR)  # geriye uyumluluk

RISKY_CMD = re.compile(
    r"(^|[\s;&|(])(rm|rmdir|sudo|su|dd|mkfs|diskutil|shutdown|reboot|halt|kill|killall|pkill|launchctl|"
    r"chown|crontab|ssh|scp|rsync|security|defaults\s+write|networksetup|scutil|osascript|csrutil|"
    r"brew\s+(uninstall|remove)|pip\s+uninstall|npm\s+(publish|unpublish)|git\s+(push|reset\s+--hard|clean|"
    r"checkout\s+--|rebase|branch\s+-D)|docker\s+(rm|rmi|system\s+prune)|trash|srm|shred|truncate)\b"
    r"|\|\s*(sh|bash|zsh)\b|(^|[^>])>(?!>)|chmod\s+-R|find\b.*-delete|\bmv\b.*\s-f\b"
    r"|curl\b.*(-X\s*(POST|PUT|DELETE)|--data|-d\s)", re.I)


def command_is_risky(command: str) -> bool:
    if RISKY_CMD.search(command):
        return True
    return any(x in command for x in PROTECTED)


@tool("read_file", "Ev klasöründeki bir dosyayı oku (metin, kod, docx, rtf).", {"path": {"type": "string"}}, ["path"])
def read_file(path: str) -> str:
    p = _home_path(path)
    if not p.is_file():
        return "Dosya yok."
    if p.suffix.lower() in (".docx", ".doc", ".rtf", ".odt", ".pages", ".html"):
        return _run(["textutil", "-convert", "txt", "-stdout", str(p)])[:8000]
    return p.read_text(errors="replace")[:8000]


@tool("run_command", "Mac'te terminal komutu çalıştır (zsh, ev klasöründe). Normal komutlar hemen çalışır; "
      "silme, sudo, push, kill gibi riskli olanlar onay bekler.",
      {"command": {"type": "string"}, "cwd": {"type": "string", "description": "çalışma klasörü, örn. ~/projects/uygulamam"}},
      ["command"],
      confirm=lambda a: command_is_risky(a.get("command", "")),
      summary=lambda a: f"Komut çalıştır: `{a.get('command')}` ({a.get('cwd') or '~'})")
def run_command(command: str, cwd: str = "") -> str:
    d = _home_path(cwd or "~")
    shell = "/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh"
    return _run([shell, "-lc", command], d, timeout=600)


@tool("system_status", "Mac'in durumu: ne zamandır açık, pil, disk, bellek.")
def system_status() -> str:
    up = _run(["uptime"])
    m = re.search(r"up\s+(.+?),\s+\d+\s+users?", up)
    batt = re.search(r"(\d+)%;\s*([^;]+)", _run(["pmset", "-g", "batt"]))
    df = _run(["df", "-h", "/"]).splitlines()
    disk = df[-1].split() if len(df) > 1 else []
    lines = [f"Mac açık kalma süresi: {m.group(1) if m else up}"]
    if batt:
        lines.append(f"Pil: %{batt.group(1)} ({batt.group(2).strip()})")
    if len(disk) >= 5:
        lines.append(f"Disk: {disk[1]} toplam, {disk[3]} boş (%{disk[4].rstrip('%')} dolu)")
    return "\n".join(lines)


@tool("running_processes", "Mac'te çalışan programlar/süreçler: en çok CPU ve bellek kullananlar ya da isme göre ara "
      "(örn. 'Claude', 'python', 'ollama').", {"name": {"type": "string", "description": "isteğe bağlı filtre"}})
def running_processes(name: str = "") -> str:
    out = _run(["ps", "-axo", "pid=,pcpu=,pmem=,etime=,comm="], timeout=15)
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        pid, cpu, mem, etime, comm = parts
        app = Path(comm).name
        if name and name.lower() not in comm.lower():
            continue
        rows.append((float(cpu), float(mem), pid, etime, app))
    rows.sort(reverse=True)
    if not rows:
        return f"'{name}' adında çalışan bir süreç yok." if name else "Süreç listesi alınamadı."
    return "\n".join(f"{app} (pid {pid}) CPU %{cpu:.0f}, bellek %{mem:.1f}, {etime} süredir çalışıyor"
                     for cpu, mem, pid, etime, app in rows[:15])


# ---------------------------------------------------------------- projeler
@tool("projects_overview", "~/projects altındaki tüm git projelerinin durumu: son commit, kaç gündür dokunulmadı, "
      "commitlenmemiş dosya sayısı.")
def projects_overview() -> str:
    lines = []
    for d in sorted(config.PROJECTS_DIR.iterdir()):
        if not (d / ".git").exists():
            continue
        last = _run(["git", "log", "-1", "--format=%ct|%s"], d)
        dirty = _run(["git", "status", "--porcelain"], d)
        n_dirty = 0 if dirty.startswith("(çıktı yok") else len(dirty.splitlines())
        try:
            ts, msg = last.split("|", 1)
            days = int((time.time() - int(ts)) / 86400)
            lines.append(f"{d.name}: {days} gün önce — \"{msg[:60]}\" · {n_dirty} değişmiş dosya")
        except ValueError:
            lines.append(f"{d.name}: commit yok")
    return "\n".join(lines) or "Git projesi bulunamadı."


@tool("project_activity", "Bir projenin son N gündeki commitleri.",
      {"project": {"type": "string"}, "days": {"type": "integer"}}, ["project"])
def project_activity(project: str, days: int = 7) -> str:
    d = _inside_projects(project)
    return _run(["git", "log", f"--since={days} days ago", "--format=%ad %s", "--date=short"], d)


# ---------------------------------------------------------------- dünya: hava durumu, saat, web araması
WMO_TR = {0: "açık", 1: "çoğunlukla açık", 2: "parçalı bulutlu", 3: "kapalı", 45: "sisli", 48: "kırağılı sis",
          51: "hafif çisenti", 53: "çisenti", 55: "yoğun çisenti", 56: "dondurucu çisenti", 57: "dondurucu çisenti",
          61: "hafif yağmurlu", 63: "yağmurlu", 65: "şiddetli yağmurlu", 66: "dondurucu yağmur", 67: "dondurucu yağmur",
          71: "hafif karlı", 73: "karlı", 75: "yoğun karlı", 77: "kar taneli", 80: "hafif sağanak", 81: "sağanak",
          82: "şiddetli sağanak", 85: "kar sağanağı", 86: "yoğun kar sağanağı", 95: "gök gürültülü fırtına",
          96: "dolulu fırtına", 99: "şiddetli dolulu fırtına"}


def _http():
    import httpx
    return httpx.Client(timeout=15, follow_redirects=True)


@tool("get_weather", "Bir şehrin ŞU ANKİ hava durumu ve bugünün/yarının tahmini (gerçek veri, Open-Meteo). "
      "Hava sorulduğunda MUTLAKA bunu kullan.",
      {"city": {"type": "string", "description": "Şehir adı; boşsa kullanıcının şehri"},
       "days": {"type": "integer", "description": "Kaç günlük tahmin (1-7), varsayılan 2"}})
def get_weather(city: str = "", days: int = 2) -> str:
    city = city or config.HOME_CITY
    days = max(1, min(int(days or 2), 7))
    with _http() as h:
        g = h.get("https://geocoding-api.open-meteo.com/v1/search",
                  params={"name": city, "count": 1, "language": "tr"}).json()
        if not g.get("results"):
            return f"'{city}' diye bir şehir bulamadım."
        loc = g["results"][0]
        f = h.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": loc["latitude"], "longitude": loc["longitude"], "timezone": "auto", "forecast_days": days,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max",
            "wind_speed_unit": "ms"}).json()
    c, d = f["current"], f["daily"]
    lines = [f"{loc['name']}, {loc.get('country', '')} — şu an ({c['time'].replace('T', ' ')}): "
             f"{c['temperature_2m']:.0f}°C, hissedilen {c['apparent_temperature']:.0f}°C, "
             f"{WMO_TR.get(c['weather_code'], 'bilinmiyor')}, rüzgar {c['wind_speed_10m']:.0f} m/s"]
    for i, day in enumerate(d["time"]):
        lines.append(f"{day}: {d['temperature_2m_min'][i]:.0f}…{d['temperature_2m_max'][i]:.0f}°C, "
                     f"{WMO_TR.get(d['weather_code'][i], 'bilinmiyor')}, yağış ihtimali "
                     f"%{d['precipitation_probability_max'][i] or 0}")
    return "\n".join(lines)


@tool("get_time", "Şu anki tarih ve saat (Kullanıcının saat diliminde).")
def get_time() -> str:
    import datetime as dt
    now = dt.datetime.now()
    return now.strftime("%d.%m.%Y %H:%M, ") + ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma",
                                                "Cumartesi", "Pazar"][now.weekday()]


def _google_news(query: str, n: int = 6) -> list[dict]:
    import xml.etree.ElementTree as ET
    with _http() as h:
        r = h.get("https://news.google.com/rss/search", params={"q": query, "hl": "tr", "gl": "TR", "ceid": "TR:tr"})
    items = ET.fromstring(r.content).findall(".//item")[:n]
    return [{"title": i.findtext("title"), "body": (i.findtext("pubDate") or "")[:22],
             "href": i.findtext("link")} for i in items]


def _wikipedia(query: str) -> list[dict]:
    with _http() as h:
        r = h.get("https://tr.wikipedia.org/w/api.php", params={
            "action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 3})
    return [{"title": x["title"], "body": re.sub(r"<[^>]+>", "", x.get("snippet", "")),
             "href": "https://tr.wikipedia.org/wiki/" + x["title"].replace(" ", "_")}
            for x in r.json().get("query", {}).get("search", [])]


@tool("web_search", "İnternette ara (güncel olaylar, fiyatlar, kişiler, bilmediğin her şey). Kısa sonuçlar döner; "
      "ayrıntı için read_webpage ile linki oku.", {"query": {"type": "string"}}, ["query"])
def web_search(query: str) -> str:
    errors = []
    for name, fn in (("DuckDuckGo", lambda q: list(__import__("ddgs").DDGS().text(q, region="tr-tr", max_results=5))),
                     ("Google Haberler", _google_news), ("Wikipedia", _wikipedia)):
        try:
            res = fn(query)
            if res:
                return f"[{name}]\n" + "\n".join(
                    f"- {r.get('title')}: {(r.get('body') or '')[:220]} ({r.get('href')})" for r in res)
        except Exception as e:
            errors.append(f"{name}: {type(e).__name__}")
    return "Arama sonuç vermedi" + (f" ({'; '.join(errors)})" if errors else "") + "."


@tool("news", "Güncel haber başlıkları (Google Haberler). Bir olay, şehir ya da konu hakkında son haberler için.",
      {"query": {"type": "string", "description": "örn. 'İzmir patlama' ya da 'Türkiye gündem'"}}, ["query"])
def news(query: str) -> str:
    try:
        res = _google_news(query, 8)
    except Exception as e:
        return f"Haberlere ulaşılamadı ({type(e).__name__}: {str(e)[:80]})."
    if not res:
        return "Bu konuda haber bulunamadı."
    return "\n".join(f"- {r['title']} ({r['body']}) {r['href']}" for r in res)


# ---------------------------------------------------------------- projeler hakkında bilgi (bilgi bankası)
def knowledge_slugs() -> list[str]:
    """knowledge/ klasöründeki not dosyalarının adları (uzantısız). Her dosya bir projeyi anlatır."""
    if not config.KNOWLEDGE_DIR.is_dir():
        return []
    return sorted(p.stem for p in config.KNOWLEDGE_DIR.glob("*.md") if p.stem.lower() != "readme")


def _slug_for(name: str) -> str | None:
    key = re.sub(r"[\s_-]+", "", name.lower())
    for slug in knowledge_slugs():
        k = re.sub(r"[\s_-]+", "", slug.lower())
        if k and (k in key or key in k):
            return slug
    return None


@tool("project_info", "Kullanıcının bir projesi hakkında gerçek bilgi: ne olduğu, özellikleri, durumu. "
      "Projeler hakkındaki sorularda MUTLAKA önce bunu kullan.", {"name": {"type": "string"}}, ["name"])
def project_info(name: str) -> str:
    slug = _slug_for(name)
    parts = []
    if slug:
        parts.append((config.KNOWLEDGE_DIR / f"{slug}.md").read_text(errors="replace"))
    folder = config.PROJECTS_DIR / (slug or name.strip())
    if folder.is_dir():
        for readme in ("README_TR.md", "README.md"):
            f = folder / readme
            if f.exists():
                parts.append(f"[{folder.name}/{readme} başı]\n" + f.read_text(errors="replace")[:1500])
                break
    known = ", ".join(knowledge_slugs())
    return "\n\n".join(parts) or (f"'{name}' hakkında kayıtlı bilgi yok." + (f" Bilinen projeler: {known}" if known else ""))


# ---------------------------------------------------------------- çalıştırıcı
def schemas() -> list[dict]:
    return [t.schema() for t in TOOLS.values()]


def call(name: str, args: dict) -> tuple[str, int | None]:
    """Aracı çalıştırır. Onay gerekiyorsa (mesaj, pending_id) döner ve çalıştırmaz."""
    t = TOOLS.get(name)
    if not t:
        return f"Bilinmeyen araç: {name}", None
    args = {k: v for k, v in (args or {}).items() if k in t.params}
    if t.confirm(args):
        pid = db.create_pending(name, args, t.summary(args))
        return (f"ONAY BEKLİYOR (#{pid}): {t.summary(args)}. Sahibe onaylaması gerektiğini söyle; "
                "kendin tekrar deneme."), pid
    return execute(name, args), None


def execute(name: str, args: dict) -> str:
    try:
        return TOOLS[name].fn(**args)
    except PermissionError as e:
        return f"İzin yok: {e}"
    except Exception as e:  # araç hatası modele geri dönsün, sunucu çökmesin
        return f"Hata: {type(e).__name__}: {e}"


from . import mac  # noqa: E402,F401  Mac araçlarını kaydeder
from . import home  # noqa: E402,F401  ev araçlarını (Apple TV) kaydeder
