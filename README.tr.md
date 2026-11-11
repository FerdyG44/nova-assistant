# N.O.V.A. — Mac'inde yaşayan kişisel yapay zeka asistanı

**"Nova"** diye seslen: ekranda piksellerden oluşan bir yüz belirir, seni dinler, dudakları sesle senkron
konuşarak cevap verir ve bilgisayarında senin yerine iş yapar. Dışarıdayken telefondan konuşursun.

Nova tek bir kişinin günlük kullanımı için yazıldı ve kendi asistanını kurabilmen için olduğu gibi paylaşılıyor.
Varsayılan dili **Türkçe**; istersen başka dillerde de cevap verir.

## Neler yapabilir

- **Konuşma:** isimle uyandırma, ses tanıma (Whisper), doğal sinir ağı sesleri, "Nova, dur" ile söz kesme
- **Yüz:** tarayıcıda çizilen 3B piksel kafa; ifadeler (mutlu, üzgün, şaşkın, meraklı, göz kırpma), gerçek sese
  göre dudak senkronu, açılışta toplanma ve kapanışta dağılma animasyonu
- **Bilgisayar:** uygulama açma/kapatma, dosya arama/okuma/yazma, terminal komutları, ekranı okuma (OCR), tıklama,
  klavye, pano, ses, müzik, takvim, hatırlatıcı, zamanlayıcı, e-posta taslağı
- **Dünya:** hava durumu, saat, internette arama, sayfa okuma, haberler
- **Ev:** Apple TV üzerinden televizyonu açıp kapatma, uygulama açma, oynatma kontrolü
- **İşler:** görevler, notlar, projelerinin git durumu ve basit Markdown notlarından proje bilgisi
- **Hafıza:** "bunu hatırla" ve "bundan sonra şöyle yap" dediklerini kalıcı saklar
- **Telefon:** Tailscale üzerinden çalışan küçük bir Flutter uygulaması (yazı ve sesli mesaj)

## Nasıl çalışır

```
"Nova…" ─▶ Mac mikrofonu ─▶ wake.py (isim yakalama, Whisper)
                               │  yüzü aç (Chrome kiosk, tam ekran)
                               ▼
Telefon ──Tailscale──▶ FastAPI (nova.main) ─▶ beyin (Claude API ya da lokal Ollama) + araçlar ─▶ cevap
                               │                                                          │
                               └──── SSE ─▶ face.html ◀────────── ses ────────────────────┘
```

Beyin olarak **Claude API** (önerilen: araç kullanımında çok daha güvenilir) ya da **Ollama ile lokal model**
(ücretsiz, internetsiz çalışır, daha zayıf) kullanılır. Claude'a ulaşılamazsa Nova lokal modele düşer.

## Gerekenler

- Apple Silicon işlemcili bir Mac (ses tanıma MLX kullanır; Mac araçları AppleScript ve macOS altyapısına dayanır)
- Google Chrome (ya da Brave/Edge), Python 3.12, Homebrew
- Bir Anthropic API anahtarı **ya da** lokal model için Ollama (en az 8 GB RAM, 16 GB daha iyi)
- İsteğe bağlı: Apple TV, telefon uygulaması için Tailscale ve Flutter

## Kurulum

```bash
git clone <bu repo> nova && cd nova
cp .env.example .env
sed -i '' "s/buraya-uzun-rastgele-bir-token/$(openssl rand -hex 24)/" .env
open -e .env          # NOVA_OWNER, NOVA_HOME_CITY ve (istersen) ANTHROPIC_API_KEY değerlerini yaz
./start.sh            # ilk çalıştırmada Python ortamını kurar, paketleri yükler
```

İlk açılışta macOS **mikrofon** izni sorar. Sonra "Nova" de. Ses modelleri bir kereliğine iner (~500 MB).

Sistem Ayarları → Gizlilik ve Güvenlik altında, Nova'yı başlattığın uygulamaya (Terminal) bir kez izin ver:

| İzin | Ne için |
|---|---|
| Mikrofon | "Nova" diye seslenmek ve sesli komutlar |
| Erişilebilirlik | tıklama, yazma, klavye kısayolları |
| Ekran Kaydı | ekranı okuma |
| Otomasyon | Müzik, Takvim, Hatırlatıcılar, Finder, Mail (ilk kullanımda macOS sorar) |

### Yüzü kurulum yapmadan görmek

`server/nova/static/face.html` dosyasını Chrome'da aç ve adresin sonuna `?demo` ekle. 1–7 tuşları ifade değiştirir.

### Nova'ya projelerini anlat

`knowledge/` klasörüne her proje için bir Markdown dosyası koy (`knowledge/README.md` dosyasına bak). Kendi
notların git'e girmez.

### Apple TV (isteğe bağlı)

```bash
server/.venv/bin/atvremote wizard     # Apple TV'ni seç, televizyonda çıkan PIN'leri gir
```

Sonra: "Nova, televizyonu aç", "televizyonda YouTube'u aç", "müzik aç", "televizyonu kapat". Televizyonun kendisi,
Apple TV uyanıp uyurken HDMI-CEC ile açılıp kapanır.

### Telefondan erişim (isteğe bağlı)

```bash
brew install --cask tailscale && tailscale serve --bg 8800
cd app && flutter create --platforms=ios,android . && flutter pub get && flutter run --release
```

Uygulamada `https://…ts.net` adresini ve `.env` içindeki token'ı gir.

### Hep açık kalsın

```bash
sed "s|NOVA_DIR|$PWD|g" deploy/com.nova.assistant.plist > ~/Library/LaunchAgents/com.nova.assistant.plist
launchctl load ~/Library/LaunchAgents/com.nova.assistant.plist
```

## Güvenlik — çalıştırmadan önce oku

Nova terminal komutu çalıştırabilir, ekranına tıklayabilir ve dosyalarını değiştirebilir. Amaç bu, ama aynı
zamanda bir risk.

- Her istek `.env` içindeki token'ı ister. **`.env` dosyanı kimseyle paylaşma, git'e ekleme.** Sunucu yalnızca
  127.0.0.1'i dinler; dışarıdan erişim kendi Tailscale ağın üzerinden olur.
- Okuma işleri hemen çalışır. Riskli işler (silme, üzerine yazma, `sudo`, `git push`, uygulama kapatma,
  "Gönder/Sil/Satın al" düğmeleri, özel AppleScript) senin onayını bekler.
- Dosya ve komut erişimi ev klasörünle sınırlıdır; şifre ve anahtar klasörleri (`.ssh`, Keychains, `.env`…) yasaktır.
- Claude API kullanırsan söylediklerin ve araç sonuçları Anthropic'e gider. Lokal modelde her şey Mac'inde kalır.
  Edge sesleri okunan metni Microsoft'a gönderir; Piper tamamen lokaldir.
- Risk sınıflandırması bir güvenlik kafesi değil, elinden geleni yapan bir süzgeçtir. Kullanım kendi sorumluluğundadır.

## Dosyalar

| Yol | Ne |
|---|---|
| `server/nova/agent.py` | kişilik ve araç kullanan sohbet döngüsü |
| `server/nova/claude_brain.py` | Claude API beyni |
| `server/nova/tools.py` | görevler, notlar, hafıza, dosyalar, terminal, hava, arama, proje notları |
| `server/nova/mac.py` | uygulamalar, ekran okuma/tıklama, klavye, müzik, takvim, hatırlatıcılar |
| `server/nova/home.py` | Apple TV / televizyon |
| `server/nova/wake.py` | isimle uyandırma, sohbet akışı, söz kesme |
| `server/nova/voice.py` | ses tanıma ve seslendirme |
| `server/nova/face.py`, `static/face.html` | yüz ve olay köprüsü |
| `app/` | Flutter telefon uygulaması |

Testler: `cd server && .venv/bin/python -m pytest -q`

## Lisans

MIT — [LICENSE](LICENSE) dosyasına bak.
