# Fontify El Yazısı API

Font yükleme, el yazısı karakterlerini çıkarma, yapay zekâ ile belge planlama,
Copilot düzenleme ve PDF üretme servisidir. Render üzerinde Docker servisi olarak
çalışır; kimlik doğrulama ve kalıcı Copilot belgeleri Firebase ile yönetilir.

## Temel endpoint'ler

- `GET /health`: servis ve bağımlılık durumu
- `POST /process`: iki form görselinden karakterleri çıkarıp font oluşturma
- `POST /api/ai/plan`: yapay zekâ destekli belge planı hazırlama
- `POST /api/ai_layout_pdf`: yerleşim planından PDF üretme
- `/api/ai/documents/*`: kalıcı Copilot belge, düzenleme ve geçmiş işlemleri

Korunan endpoint'lerde `Authorization: Bearer <Firebase ID token>` başlığı
zorunludur. İstemciden gelen kullanıcı kimliği yetki kaynağı olarak kabul edilmez.

## Render yapılandırması

Önemli ortam değişkenleri:

- `FIREBASE_CREDENTIALS`: Firebase Admin SDK servis hesabı JSON'u
- `FRONTEND_ORIGINS`: izin verilen Cloudflare alan adları
- `DEFAULT_USER_CREDITS`: yeni kullanıcı başlangıç kredisi
- `GEMINI_API_KEY`: Gemini API anahtarı
- `GROQ_API_KEY`: Groq API anahtarı
- `OPENAI_API_KEY`: OpenAI Platform API anahtarı
- `OPENROUTER_API_KEY`: OpenRouter API anahtarı
- `GROQ_MODEL_TIMEOUT_MS`: her Groq modeli için zaman aşımı; varsayılan `30000`
- `RECAPTCHA_REQUIRED`: `true` ise reCAPTCHA hataları yüklemeyi durdurur; varsayılan `false`
- `OPENAI_MODEL`: varsayılan `gpt-5.6-luna`
- `OPENROUTER_MODEL`: varsayılan `openrouter/free`
- `AI_DOCUMENT_PROVIDER_ORDER`: varsayılan `gemini,groq,openai,openrouter`
- `COPILOT_PROVIDER_ORDER`: varsayılan `groq,gemini,openai,openrouter`

Anahtarlar kaynak koda veya frontend'e yazılmaz. Sağlayıcılar sırayla denenir;
kota, bağlantı veya servis hatasında yapılandırılmış sonraki sağlayıcıya geçilir.
Groq içinde modeller `openai/gpt-oss-120b`, `openai/gpt-oss-20b`,
`llama-3.3-70b-versatile`, `llama-3.1-8b-instant` ve
`meta-llama/llama-4-scout-17b-16e-instruct` sırasıyla denenir. 429 alan model
`retry-after` süresince atlanır; 401/403 ve normal 400 hataları zinciri durdurur.
ChatGPT aboneliği OpenAI API kredisi sağlamaz; `OPENAI_API_KEY`, OpenAI Platform
üzerinden ayrıca oluşturulmalıdır.

## Yerel doğrulama

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Firebase güvenlik kuralları ayrı olarak `firebase_tmp` dizinindeki emülatör testleri
ile doğrulanır. Canlıya almadan önce Render secret'larını ve Cloudflare origin
değerlerini panelden kontrol edin.
