"""Nova ayarları. Hepsi ortam değişkeninden ya da ~/projects/nova/.env dosyasından okunur."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # ~/projects/nova


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env(ROOT / ".env")

TOKEN = os.environ.get("NOVA_TOKEN", "")                       # telefon bu token'la bağlanır
HOST = os.environ.get("NOVA_HOST", "127.0.0.1")                 # Tailscale IP'si önerilir
PORT = int(os.environ.get("NOVA_PORT", "8800"))

# Beyin: "claude" (Claude API, önerilen) ya da "ollama" (lokal, ücretsiz). Claude'a ulaşılamazsa otomatik lokale düşer.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
BRAIN = os.environ.get("NOVA_BRAIN", "claude" if ANTHROPIC_API_KEY else "ollama")
CLAUDE_MODEL = os.environ.get("NOVA_CLAUDE_MODEL", "claude-sonnet-5-5")   # daha ucuz/hızlı: claude-haiku-5-5
CLAUDE_MAX_TOKENS = int(os.environ.get("NOVA_CLAUDE_MAX_TOKENS", "1024"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("NOVA_MODEL", "qwen3:8b")                # 8 GB RAM'de qwen3:4b

HOME_DIR = Path(os.environ.get("NOVA_HOME_DIR", str(Path.home())))
PROJECTS_DIR = Path(os.environ.get("NOVA_PROJECTS_DIR", str(Path.home() / "projects")))
KNOWLEDGE_DIR = Path(os.environ.get("NOVA_KNOWLEDGE_DIR", str(ROOT / "knowledge")))   # projelerin hakkında notlar (*.md)

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "nova.sqlite3"
AUDIO_DIR = DATA_DIR / "audio"

STT_ENGINE = os.environ.get("NOVA_STT_ENGINE", "mlx")              # mlx (Apple GPU, önerilen) | faster
MLX_WHISPER_MODEL = os.environ.get("NOVA_MLX_WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
WHISPER_MODEL = os.environ.get("NOVA_WHISPER_MODEL", "small")    # Türkçe için base yetersiz
TTS_ENGINE = os.environ.get("NOVA_TTS_ENGINE", "edge")             # edge (önerilen) | piper | say
EDGE_VOICE = os.environ.get("NOVA_EDGE_VOICE", "tr-TR-EmelNeural")   # kadın; erkek: tr-TR-AhmetNeural
EDGE_RATE = os.environ.get("NOVA_EDGE_RATE", "+5%")                 # konuşma hızı
EDGE_PITCH = os.environ.get("NOVA_EDGE_PITCH", "+0Hz")              # ses tonu
PIPER_VOICE = os.environ.get("NOVA_PIPER_VOICE", "tr_TR-fahrettin-medium")  # ya da tr_TR-dfki-medium, tr_TR-fettah-medium
PIPER_SPEED = float(os.environ.get("NOVA_PIPER_SPEED", "0.95"))    # <1 daha hızlı, >1 daha yavaş
TTS_VOICE = os.environ.get("NOVA_TTS_VOICE", "Yelda")           # yedek: macOS `say` sesi

# Onaysız çalışabilen komutlar (sadece okuma). Diğer her şey onay ister.
SAFE_COMMANDS = {"ls", "cat", "head", "tail", "wc", "df", "du", "uptime", "pwd", "git", "grep", "find"}
SAFE_GIT = {"status", "log", "diff", "branch", "show"}

OWNER = os.environ.get("NOVA_OWNER", "")                         # sana nasıl hitap etsin (adın)
HOME_CITY = os.environ.get("NOVA_HOME_CITY", "İstanbul")         # hava durumu için varsayılan şehir
# Ses tanımaya ipucu: sık kullandığın özel isimler (proje, uygulama adları), virgülle
STT_HINTS = os.environ.get("NOVA_STT_HINTS", "")

# Ev: Apple TV (televizyon ona HDMI ile bağlı). Eşleştirme: server/.venv/bin/atvremote wizard
APPLETV_NAME = os.environ.get("NOVA_APPLETV_NAME", "")           # evde birden fazla Apple TV varsa adı (örn. Salon)
TV_MUSIC_APP = os.environ.get("NOVA_TV_MUSIC_APP", "")           # boşsa: Spotify yüklüyse o, yoksa Apple Music

# Uyandırma: Mac mikrofonu "Nova" kelimesini dinler, duyunca tam ekran yüz açılır.
NAME = os.environ.get("NOVA_NAME", "Nova")
WAKE_ENABLED = os.environ.get("NOVA_WAKE", "1") == "1"
# Whisper "Nova"yı bazen farklı yazar; hepsi kabul edilir.
WAKE_WORDS = [w.strip().lower() for w in os.environ.get(
    "NOVA_WAKE_WORDS", "nova,nowa,növa,nuva,noah,nowah,nova'").split(",") if w.strip()]
WAKE_MODEL = os.environ.get("NOVA_WAKE_MODEL", "tiny")                  # isim yakalama için hızlı model
MIC_THRESHOLD = float(os.environ.get("NOVA_MIC_THRESHOLD", "0.015"))    # konuşma sayılacak ses seviyesi (RMS)
END_SILENCE = float(os.environ.get("NOVA_END_SILENCE", "1.7"))   # bu kadar sn susunca cümlen bitti sayılır
BARGE_IN = os.environ.get("NOVA_BARGE_IN", "1") == "1"            # 0: konuşurken "dur" dinlenmez (video çekerken)
BARGE_IN_FACTOR = float(os.environ.get("NOVA_BARGE_IN_FACTOR", "2.0"))  # konuşurken söz kesmek için daha yüksek ses eşiği
FOLLOWUP_SECONDS = float(os.environ.get("NOVA_FOLLOWUP_SECONDS", "8"))  # cevaptan sonra isim söylemeden devam süresi
BROWSER = os.environ.get("NOVA_BROWSER", "Google Chrome")              # yüzü tam ekran açacak tarayıcı

KEEP_ALIVE = os.environ.get("NOVA_KEEP_ALIVE", "2h")                # model bellekte kalsın, her soruda yeniden yüklenmesin
MAX_REPLY_TOKENS = int(os.environ.get("NOVA_MAX_REPLY_TOKENS", "220"))

MAX_TOOL_ROUNDS = 6
HISTORY_TURNS = 12
HISTORY_MINUTES = float(os.environ.get("NOVA_HISTORY_MINUTES", "30"))  # bundan eski konuşmalar hatırlanmaz

for d in (DATA_DIR, AUDIO_DIR):
    d.mkdir(parents=True, exist_ok=True)
