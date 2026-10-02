# ai EasySaz API

وب‌سرویس هوش مصنوعی ai EasySaz که از طریق API در دسترس است.

**راهنمای آنلاین:** بعد از استقرار، آدرس `https://دامنه/doc` را باز کنید. این صفحه راهنمای کامل فارسی (کلید، مسیرها، نمونه‌کد curl / Python / JavaScript / PHP، تصویر، خطاها) را نشان می‌دهد و فرمی دارد که کاربر با کلید خودش از همان‌جا درخواست واقعی بفرستد (یک‌جا، استریم یا WebSocket) و جواب را ببیند. باز کردن آدرس اصلی سایت در مرورگر هم به `/doc` می‌رود. فایل صفحه: [web/doc.html](web/doc.html). نسخهٔ متنی راهنما: [docs/index.md](docs/index.md).

| روش | مسیر | توضیح |
|---|---|---|
| WebSocket | `/ws` | پاسخ استریم، مکالمهٔ چندمرحله‌ای (هر اتصال = یک مکالمه). نیاز به سرور ASGI |
| SSE | `POST /api/chat/stream` | استریم روی HTTP معمولی؛ روی Passenger کار می‌کند |
| JSON | `POST /api/chat` | یک درخواست، یک پاسخ کامل |
| — | `GET /health` | بدون احراز هویت |
| — | `GET /doc` | راهنمای کاربر + ارسال درخواست آزمایشی از مرورگر (بدون احراز هویت؛ کلید را کاربر در صفحه وارد می‌کند) |
| — | `GET /openapi.json` | مشخصات OpenAPI 3.1 برای Postman / Swagger |
| — | `GET /` | اطلاعات سرویس و محدودیت‌ها به‌صورت JSON؛ مرورگر به `/doc` هدایت می‌شود |

## تکنولوژی و چرا

- **Starlette** (ASGI): هم WebSocket دارد و هم HTTP/SSE، بدون وابستگی سنگین (pydantic ندارد)، مناسب هاست اشتراکی.
- **a2wsgi**: ASGI را به WSGI تبدیل می‌کند، چون **Passenger فقط WSGI می‌فهمد**.
- **requests + websockets**: کتابخانه‌های ارتباط با موتور هوش مصنوعی.

> ⚠️ **محدودیت مهم:** روی «Setup Python App» (Passenger) در cPanel، WebSocket واقعی ممکن نیست؛ Passenger برای پایتون فقط WSGI را اجرا می‌کند و آپگرید WebSocket را پشتیبانی نمی‌کند. بنابراین روی هاست اشتراکی از `POST /api/chat/stream` (SSE) برای استریم استفاده کنید. همان کد، روی VPS یا هر میزبان ASGI، `/ws` را هم سرو می‌کند.
> اگر `/ws` را روی Passenger باز کنید خطا می‌گیرید؛ درخواست HTTP معمولی به `/ws` هم پیام راهنما با کد 426 برمی‌گرداند.

## پرسیدن سوال از ترمینال

```bash
./ask.sh "your question"              # چاپ جواب
./ask.sh -s "your question"           # استریم
./ask.sh -i https://site/pic.png "what is this?"   # تصویر
```

کلید را خودش از `.env` می‌خواند؛ آدرس پیش‌فرض داخل فایل است (`-u` یا `ASK_URL` برای تغییر). اگر https در دسترس نباشد با هشدار روی http تلاش می‌کند.

## آپدیت روی هاست با یک دستور

در ترمینال cPanel:

```bash
source /home/wmkmbrcs/virtualenv/Api/3.12/bin/activate && cd /home/wmkmbrcs/Api && curl -fsSL https://raw.githubusercontent.com/dezhcode/Web-Service/ccr-ab4c9b4d-b7wg94/update.sh | bash
```

اسکریپت [update.sh](update.sh) آخرین کد را از گیت‌هاب می‌گیرد، `requirements.txt` را نصب می‌کند، چک می‌کند برنامه بالا می‌آید، Passenger را ری‌استارت می‌کند (`tmp/restart.txt`) و در پایان `/health` و `/doc` سایت را تست می‌کند. بعد از اولین اجرا، `./update.sh` هم کافی است.

- `.env` و فایل‌های دیگری که در گیت نیستند دست نمی‌خورند. اگر فایل‌های پروژه را روی هاست دستی تغییر داده باشید، تغییراتتان در `.update-backup-*.patch` ذخیره و با نسخهٔ گیت‌هاب جایگزین می‌شود.
- اگر کد جدید خطا بدهد و بالا نیاید، نسخهٔ قبلی خودکار برمی‌گردد و ری‌استارت انجام نمی‌شود.
- گزینه‌ها: `-b BRANCH` (شاخهٔ دیگر)، `-u URL` (آدرس سایت برای تست)، `--no-check`.

## نصب و تست خودکار (پیشنهادی)

بعد از گرفتن پروژه روی هاست و فعال کردن virtualenv، فقط این را اجرا کنید:

```bash
python setup_and_test.py
```

اسکریپت خودش: کتابخانه‌ها را نصب می‌کند، کلید `API_KEY` تصادفی می‌سازد و در `.env` می‌گذارد (کلید قبلی را نگه می‌دارد)، تست‌های بدون‌نیاز به موتور هوش مصنوعی را اجرا می‌کند، دسترسی هاست به آن و یک چت واقعی را تست می‌کند، Passenger را ری‌استارت می‌کند، آدرس عمومی را تست می‌کند (health، احراز هویت، چت، SSE) و در پایان کلید را چاپ می‌کند.

گزینه‌ها: `--url https://دامنه` (آدرس تست؛ پیش‌فرض داخل اسکریپت است) ، `--rotate` (کلید جدید) ، `--no-install` ، `--skip-live` ، `--no-url` ، `--no-restart`.
خروجی عمداً انگلیسی/ASCII است چون ترمینال cPanel متن راست‌به‌چپ را درست نشان نمی‌دهد. اگر هر تستی `FAIL` شد، کد خروج ۱ است و پیام خطا راه‌حل را می‌گوید.

## استقرار روی cPanel (Setup Python App / Passenger)

1. فایل‌ها را در یک پوشه روی هاست آپلود کنید (مثلاً `~/easysaz-api`).
2. cPanel → **Setup Python App** → Create Application:
   - Python version: ۳٫۹ یا بالاتر
   - Application root: `easysaz-api`
   - Application URL: دامنه/زیردامنهٔ دلخواه
   - Application startup file: `passenger_wsgi.py`
   - Application Entry point: `application`
3. در همان صفحه **Environment variables** را اضافه کنید (یا فایل `.env` بسازید، نمونه: `.env.example`):
   - `API_KEY` = یک رشتهٔ تصادفی طولانی (**اجباری**؛ بدون آن سرویس 503 می‌دهد)
4. در ترمینال/کادر «Configuration files» → `requirements.txt` را اضافه و **Run Pip Install** بزنید.
5. **Restart** کنید (یا `touch tmp/restart.txt`).
6. تست: `curl https://YOUR-DOMAIN/health` و در مرورگر `https://YOUR-DOMAIN/doc`

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
| `MAX_CONCURRENCY` | `3` | حداکثر درخواست همزمان به موتور هوش مصنوعی (هاست اشتراکی منابع کمی دارد) |
| `MAX_PROMPT_CHARS` | `8000` | |
| `MAX_IMAGE_BYTES` | `10485760` | |
| `CORS_ORIGINS` | خالی | لیست origin ها با کاما، برای فراخوانی از مرورگر (صفحهٔ `/doc` روی همان دامنه است و به این نیاز ندارد) |
| `WEBSOCKET_ENABLED` | `1` | `passenger_wsgi.py` خودش آن را `0` می‌کند تا `/doc` بگوید WebSocket در دسترس نیست |

## تست

```bash
pip install -r requirements-dev.txt
pytest
```

تست‌ها با یک سرور شبیه‌سازی‌شدهٔ محلی اجرا می‌شوند (شامل مسیر کامل WebSocket روی uvicorn و فراخوانی WSGI برای Passenger).
