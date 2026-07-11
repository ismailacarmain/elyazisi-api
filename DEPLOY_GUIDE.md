# Fontify üretim dağıtım rehberi

## Render ortam değişkenleri

Render servisinde aşağıdaki değerleri tanımlayın. Gizli değerleri repoya veya frontend koduna yazmayın.

| Değişken | Örnek / açıklama |
| --- | --- |
| `FIREBASE_CREDENTIALS` | Firebase Admin service-account JSON; Render secret olarak saklanır. |
| `RECAPTCHA_SECRET_KEY` | Google reCAPTCHA gizli anahtarı. |
| `FRONTEND_ORIGINS` | `https://fontify.online,https://www.fontify.online` |
| `DEFAULT_USER_CREDITS` | Yeni kullanıcı için başlangıç kredisi, örneğin `10`. |
| `ALLOW_LEGACY_MOBILE_UPLOADS` | Üretimde `false`. |
| `ALLOW_INSECURE_RECAPTCHA` | Üretimde `false`. |
| `GEMINI_API_KEY` | İsteğe bağlı sistem anahtarı; yalnızca Render secret olarak saklanır. |
| `GEMINI_ALLOWED_MODELS` | İsteğe bağlı model allowlist'i; boşsa uygulamanın güvenli varsayılanı kullanılır. |

Sistem hem BYOK hem sunucu anahtarıyla çalışır. Kullanıcı anahtarı gönderilmişse önce o kullanılır; yoksa `GEMINI_API_KEY` Render secret'ına güvenli biçimde düşülür. Anahtarlar Firestore'a veya backend loglarına kaydedilmez.

## Dağıtım sırası

1. Firebase Security Rules ve gerekli index'leri yayınlayın.
2. Backend değişikliklerini GitHub'a gönderin; Render sağlık kontrolünün `/health` için `200` verdiğini doğrulayın.
3. Frontend dosyalarını Cloudflare Pages'e gönderin.
4. Giriş yaparak kağıt fontu, iPad dijital fontu, AI planı, sayfa düzenleme ve PDF indirme akışlarını uçtan uca test edin.

## Güvenlik kontrol listesi

- Firebase Admin anahtarı ve reCAPTCHA secret yalnızca Render secret'larında bulunur.
- Firestore Rules, global font belgelerinin yazılmasını istemciden engeller; yazma işlemleri backend Admin SDK üzerinden yapılır.
- `mobile_upload_sessions` belgeleri istemciye kapalıdır ve süreli sunucu oturumu olarak kullanılır.
- CORS yalnızca `FRONTEND_ORIGINS` içindeki alan adlarını kabul eder.
- Üretimde debug ve iki insecure/legacy bayrağı kapalıdır.
- Kullanıcıya özel font ve varlık endpoint'leri Firebase ID token doğrulaması yapar.

## Yerel geliştirme

Yerel testte reCAPTCHA secret yoksa yalnızca debug modunda veya açıkça `ALLOW_INSECURE_RECAPTCHA=true` verilerek bypass yapılabilir. Bu değer üretime taşınmamalıdır.
