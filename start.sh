#!/bin/zsh
# Nova'yı başlatır. İlk çalıştırmada Python ortamını kurar; lokal model seçiliyse Ollama'yı da hazırlar.
set -e
cd "$(dirname "$0")"
[ -f .env ] || { echo "Önce ayar dosyasını oluştur:  cp .env.example .env  (ve içini doldur)"; exit 1; }
val() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//'; }

# 1) Beyin: Claude anahtarı yoksa lokal model (Ollama) gerekir
if [ -z "$(val ANTHROPIC_API_KEY)" ]; then
  MODEL=$(val NOVA_MODEL); MODEL=${MODEL:-qwen3:8b}
  command -v ollama >/dev/null || { echo "Lokal model için Ollama gerekli:  brew install ollama"; exit 1; }
  if ! curl -s http://127.0.0.1:11434/api/tags >/dev/null; then
    echo "→ Ollama başlatılıyor…"
    brew services start ollama >/dev/null 2>&1 || (ollama serve >/dev/null 2>&1 &)
    for i in {1..20}; do curl -s http://127.0.0.1:11434/api/tags >/dev/null && break; sleep 1; done
  fi
  if ! ollama list | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "→ $MODEL indiriliyor (birkaç GB, bir kere)…"
    ollama pull "$MODEL"
  fi
fi

# 2) Python ortamı ve paketler
cd server
if [ ! -x .venv/bin/python ]; then
  echo "→ Python ortamı kuruluyor…"
  if command -v uv >/dev/null; then uv venv .venv -q; else python3 -m venv .venv; fi
fi
if ! .venv/bin/python -c "import pyatv, py3langid, anthropic, Vision, Quartz, mlx_whisper, ddgs, edge_tts, piper, faster_whisper, sounddevice, fastapi" 2>/dev/null; then
  echo "→ Python paketleri kuruluyor (ilk seferde birkaç dakika sürer)…"
  if command -v uv >/dev/null; then
    uv pip install --python .venv/bin/python -q -r requirements.txt
  else
    .venv/bin/python -m ensurepip -q 2>/dev/null || true
    .venv/bin/python -m pip install -q -r requirements.txt
  fi
fi

echo "→ Nova başlıyor. 'Nova' diye seslen."
exec .venv/bin/python -m nova.main
