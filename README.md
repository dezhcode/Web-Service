# Copilot Web Service

وب‌سرویسی که کلاینت Copilot (حالت `smart`) را به‌صورت API ارائه می‌دهد:

| روش | مسیر | توضیح |
|---|---|---|
| WebSocket | `/ws` | پاسخ استریم، مکالمهٔ چندمرحله‌ای (هر اتصال = یک مکالمه). نیاز به سرور ASGI |
| SSE | `POST /api/chat/stream` | استریم روی HTTP معمولی؛ روی Passenger کار می‌کند |
| JSON | `POST /api/chat` | یک درخواست، یک پاسخ کامل |
| — | `GET /health` | بدون احراز هویت |

## تکنولوژی و چرا

- **Starlette** (ASGI): هم WebSocket دارد و هم HTTP/SSE، بدون وابستگی سنگین (pydantic ندارد)، مناسب هاست اشتراکی.
- **a2wsgi**: ASGI را به WSGI تبدیل می‌کند، چون **Passenger فقط WSGI می‌فهمد**.
- **requests + websockets**: همان کتابخانه‌های اسکریپت اصلی (`copilot_client.py`).

> ⚠️ **محدودیت مهم:** روی «Setup Python App» (Passenger) در cPanel، WebSocket واقعی ممکن نیست؛ Passenger برای پایتون فقط WSGI را اجرا می‌کند و آپگرید WebSocket را پشتیبانی نمی‌کند. بنابراین روی هاست اشتراکی از `POST /api/chat/stream` (SSE) برای استریم استفاده کنید. همان کد، روی VPS یا هر میزبان ASGI، `/ws` را هم سرو می‌کند.
> اگر `/ws` را روی Passenger باز کنید خطا می‌گیرید؛ درخواست HTTP معمولی به `/ws` هم پیام راهنما با کد 426 برمی‌گرداند.

## استقرار روی cPanel (Setup Python App / Passenger)

1. فایل‌ها را در یک پوشه روی هاست آپلود کنید (مثلاً `~/copilot-service`).
2. cPanel → **Setup Python App** → Create Application:
   - Python version: ۳٫۹ یا بالاتر
   - Application root: `copilot-service`
   - Application URL: دامنه/زیردامنهٔ دلخواه
   - Application startup file: `passenger_wsgi.py`
   - Application Entry point: `application`
3. در همان صفحه **Environment variables** را اضافه کنید (یا فایل `.env` بسازید، نمونه: `.env.example`):
   - `API_KEY` = یک رشتهٔ تصادفی طولانی (**اجباری**؛ بدون آن سرویس 503 می‌دهد)
   - `COPILOT_MODE` = `smart` (پیش‌فرض)
4. در ترمینال/کادر «Configuration files» → `requirements.txt` را اضافه و **Run Pip Install** بزنید.
5. **Restart** کنید (یا `touch tmp/restart.txt`).
6. تست: `curl https://YOUR-DOMAIN/health`

## اجرا روی VPS / لوکال (با WebSocket)

```bash
pip install -r requirements.txt uvicorn
API_KEY=secret uvicorn app:app --host 0.0.0.0 --port 8000
# پشت nginx/Apache باید آپگرید WebSocket را پروکسی کنید
```

## احراز هویت

کلید را با یکی از این‌ها بفرستید: `Authorization: Bearer <key>` ، `X-API-Key: <key>` ، یا `?api_key=<key>` (برای WebSocket مرورگر که هدر نمی‌تواند بفرستد؛ کلید در لاگ‌ها دیده می‌شود، پس ترجیحاً هدر).
چند کلید را با کاما در `API_KEY` بگذارید.

## نمونه استفاده

```bash
# JSON
curl -X POST https://HOST/api/chat -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{"prompt":"سلام"}'
# → {"text": "..."}

# SSE (استریم)
curl -N -X POST https://HOST/api/chat/stream -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" -d '{"prompt":"سلام"}'
# data: {"type":"delta","text":"..."} ... data: {"type":"done"}
```

تصویر: به‌جای `prompt` یا کنار آن، `image_url` (فقط آدرس عمومی؛ آدرس‌های داخلی رد می‌شوند) یا `image_base64` بفرستید.

```js
// WebSocket
const ws = new WebSocket("wss://HOST/ws?api_key=KEY");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.onopen = () => ws.send(JSON.stringify({ type: "chat", id: "1", prompt: "سلام" }));
// ← {type:"ready"} ، {type:"delta",id,text} ... ، {type:"done",id,text}
// {type:"reset"} مکالمهٔ جدید شروع می‌کند ، {type:"ping"} → {type:"pong"}
```

## تنظیمات (متغیرهای محیطی)

| نام | پیش‌فرض | |
|---|---|---|
| `API_KEY` | — | اجباری |
| `COPILOT_MODE` | `smart` | `chat` / `smart` / `reasoning` / `search` |
| `MAX_CONCURRENCY` | `3` | حداکثر درخواست همزمان به Copilot (هاست اشتراکی منابع کمی دارد) |
| `MAX_PROMPT_CHARS` | `8000` | |
| `MAX_IMAGE_BYTES` | `10485760` | |
| `CORS_ORIGINS` | خالی | لیست origin ها با کاما، برای فراخوانی از مرورگر |

## تست

```bash
pip install -r requirements-dev.txt
pytest
```

تست‌ها با یک سرور Copilot شبیه‌سازی‌شده محلی اجرا می‌شوند (شامل مسیر کامل WebSocket روی uvicorn و فراخوانی WSGI برای Passenger).
