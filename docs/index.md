<div dir="rtl">

# راهنمای استفاده از API هوش مصنوعی ai EasySaz (پایتون)

> نسخهٔ کامل و تعاملی این راهنما روی خود سرویس است: `https://dezhcode.pyho.ir/doc` — آنجا می‌توانید بدون نوشتن کد، با کلیدتان درخواست بفرستید.

با پایتون و کتابخانهٔ `requests` سه کار می‌شود: فرستادن متن ساده، گرفتن جواب به‌صورت استریم، و فرستادن تصویر. همهٔ درخواست‌ها فقط به یک کلید API نیاز دارند.

## ۱. کلید API: چطور گرفته می‌شود

کلید را مدیر سرویس به شما می‌دهد؛ ثبت‌نام یا صفحهٔ خودکار برای گرفتن کلید وجود ندارد. از مدیر سرویس یک کلید برای خودتان بخواهید و آدرس سرویس را هم بپرسید (در این راهنما `https://dezhcode.pyho.ir` است).

- کلید یک رشتهٔ تصادفی حدود ۴۳ کاراکتری است. مثل رمز عبور با آن رفتار کنید: در کد، گیت یا پیام عمومی نگذارید.
- بهتر است برای هر برنامه یا هر نفر کلید جدا بگیرید تا اگر یکی لو رفت، فقط همان عوض شود و بقیه کار کنند.
- بدون کلید یا با کلید غلط، سرور خطای ۴۰۱ برمی‌گرداند.

کلید در هر درخواست، داخل هدر `Authorization` فرستاده می‌شود: `Authorization: Bearer <کلید شما>`. کد پایتونِ بخش بعد این کار را برای شما انجام می‌دهد.

## ۲. آماده‌سازی

فقط پایتون ۳٫۸ به بالا و کتابخانهٔ `requests` لازم است.

```bash
pip install requests
```

کلید را داخل متغیر محیطی بگذارید تا در فایل کد نماند:

```bash
export EASYSAZ_API_KEY="کلید-شما"          # Linux و macOS
$env:EASYSAZ_API_KEY="کلید-شما"          # Windows PowerShell
```

این چند خط را بالای فایل پایتونتان بگذارید. مثال‌های بعدی فرض می‌کنند این متغیرها تعریف شده‌اند:

```python
import os
import requests

BASE_URL = "https://dezhcode.pyho.ir"
API_KEY = os.environ["EASYSAZ_API_KEY"]
HEADERS = {"Authorization": f"Bearer {API_KEY}"}
```

## ۳. ارسال متن ساده

متن را در فیلد `prompt` به `/api/chat` می‌فرستید و کل جواب را یک‌جا می‌گیرید. حداکثر طول `prompt` ۸۰۰۰ کاراکتر است.

```python
def ask(prompt):
    r = requests.post(f"{BASE_URL}/api/chat", headers=HEADERS,
                      json={"prompt": prompt}, timeout=150)
    if r.status_code != 200:
        raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
    return r.json()["text"]

print(ask("Say hello in one sentence"))
```

پاسخ موفق یک JSON با یک فیلد است: `{"text": "..."}`. زمان‌دادن `timeout=150` لازم است؛ بعضی جواب‌ها چند ده ثانیه طول می‌کشند.

## ۴. استریم (جواب تکه‌تکه)

با `/api/chat/stream` جواب هم‌زمان با تولید شدن می‌رسد، نه بعد از پایان. ورودی مثل بخش قبل است. تابع زیر هر تکه متن را که می‌رسد برمی‌گرداند:

```python
import json

def ask_stream(prompt):
    with requests.post(f"{BASE_URL}/api/chat/stream", headers=HEADERS,
                       json={"prompt": prompt}, stream=True, timeout=150) as r:
        if r.status_code != 200:
            raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
        r.encoding = "utf-8"          # بدون این، متن فارسی خراب می‌شود
        for line in r.iter_lines(decode_unicode=True):
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if event["type"] == "delta":
                yield event["text"]
            elif event["type"] == "error":
                raise RuntimeError(event["message"])
            elif event["type"] == "done":
                return

for piece in ask_stream("Count from 1 to 5"):
    print(piece, end="", flush=True)
print()
```

سرور سه نوع رویداد می‌فرستد (تابع بالا همه را انجام می‌دهد):

| نوع | معنی |
| --- | --- |
| `delta` | یک تکه از متن در فیلد `text`؛ تکه‌ها را به ترتیب به هم بچسبانید |
| `done` | جواب کامل شد |
| `error` | خطا در میانهٔ کار؛ متن خطا در فیلد `message` است و جواب ناقص مانده |

اگر میان‌راه (پراکسی یا هاست) پاسخ را بافر کند، تکه‌ها یک‌جا می‌رسند. متن نهایی همان است و فقط زمان نمایش فرق می‌کند.

## ۵. ارسال تصویر

تصویر را یا با لینک (`image_url`) یا با خود فایل در قالب base64 (`image_base64`) می‌فرستید. `prompt` اختیاری است؛ اگر نگذارید سرویس می‌پرسد «این تصویر چیست؟ دقیق توضیح بده». هر دو روش روی `/api/chat` و `/api/chat/stream` کار می‌کند.

**از روی لینک:**

```python
r = requests.post(f"{BASE_URL}/api/chat", headers=HEADERS, timeout=150, json={
    "image_url": "https://example.com/photo.jpg",
    "prompt": "What is in this image?",
})
print(r.json()["text"] if r.status_code == 200 else r.text)
```

**از روی فایل روی کامپیوتر شما:**

```python
import base64

with open("photo.jpg", "rb") as f:
    img = base64.b64encode(f.read()).decode()

r = requests.post(f"{BASE_URL}/api/chat", headers=HEADERS, timeout=150, json={
    "image_base64": img,
    "prompt": "What is in this image?",
})
print(r.json()["text"] if r.status_code == 200 else r.text)
```

برای جواب استریمی به‌جای آن، همین بدنه را به `/api/chat/stream` بفرستید (همان روش بخش ۴).

| فیلد | مقدار | محدودیت |
| --- | --- | --- |
| `image_url` | لینک مستقیم تصویر با http یا https | فقط آدرس عمومی که بدون لاگین باز شود؛ آدرس داخلی رد می‌شود؛ حداکثر ۳ ریدایرکت |
| `image_base64` | base64 خالص یا آدرس `data:image/...;base64,...` | رشتهٔ معتبر base64 |
| اندازه | هر دو حالت | حداکثر ۱۰ مگابایت |

## ۶. خطاها و کدهای وضعیت

پاسخ موفق همیشه کد ۲۰۰ و `{"text": "..."}` است. هر خطای دیگر به شکل `{"error": "..."}` می‌آید؛ تابع `ask` در بخش ۳ کد و متن آن را در یک `RuntimeError` می‌گذارد.

| کد | معنی | چه کنید |
| --- | --- | --- |
| 400 | ورودی نادرست: `prompt` خالی یا بیش از ۸۰۰۰ کاراکتر، تصویر بزرگ‌تر از ۱۰ مگابایت، لینک تصویر نامعتبر یا داخلی | متن `error` می‌گوید کجا اشکال دارد؛ ورودی را اصلاح کنید |
| 401 | کلید نفرستاده‌اید یا غلط است | کلید را با مدیر سرویس چک کنید؛ ممکن است عوض شده باشد |
| 502 | ارتباط سرویس با موتور هوش مصنوعی ناموفق بود (`upstream request failed`) | چند ثانیه بعد دوباره بزنید (کد زیر) |
| 503 | سرویس هنوز کلیدی ندارد و تنظیم نشده | به مدیر سرویس خبر دهید |
| 404 با صفحهٔ HTML | آدرس سرویس اشتباه است | `BASE_URL` را با مدیر سرویس چک کنید |
| 524 یا 504 | میان‌راه پیش از پایان جواب، اتصال را قطع کرد (جواب خیلی طول کشید) | سؤال را کوتاه‌تر کنید یا از استریم استفاده کنید |

برای خطای ۵۰۲ یک تلاش مجدد منطقی است. باقی خطاها با تکرار درست نمی‌شوند:

```python
import time

def ask_with_retry(prompt, tries=3):
    for i in range(tries):
        try:
            return ask(prompt)
        except RuntimeError as e:
            if not str(e).startswith("502") or i == tries - 1:
                raise
            time.sleep(2 * (i + 1))
```

در استریم، خطاهایی که قبل از شروع جریان رخ می‌دهند (کلید، ورودی) همان کدهای بالا را می‌دهند و `ask_stream` آن را با `RuntimeError` می‌اندازد. خطای میانهٔ کار با رویداد `error` می‌آید.

## ۷. نکات مهم

مهم‌ترین نکته: هر درخواست یک مکالمهٔ تازه است و سرویس سؤالات قبلی را نمی‌داند. اگر سؤال بعدی به قبلی وابسته است، متن قبلی را خودتان در `prompt` بگنجانید:

```python
history = []

def chat(user_text):
    history.append(f"User: {user_text}")
    prompt = "\n".join(history) + "\nAssistant:"
    answer = ask(prompt)
    history.append(f"Assistant: {answer}")
    return answer

chat("My name is Sara.")
print(chat("What is my name?"))
```

وقتی تاریخچه طولانی شود، با سقف ۸۰۰۰ کاراکتر برخورد می‌کند؛ قدیمی‌ترین پیام‌ها را از `history` حذف کنید.

- **محدودیت هم‌زمانی:** سرویس حداکثر ۳ درخواست هم‌زمان را به موتور هوش مصنوعی می‌فرستد (مجموع همهٔ کاربران)؛ بقیه در صف می‌مانند، پس در شلوغی جواب ممکن است به مدت کشیده شود.
- **WebSocket:** روی این سرویس در دسترس نیست؛ استریم را با روش بخش ۴ بگیرید.
- **امنیت:** همیشه از آدرس `https` استفاده کنید تا کلید رمزنگاری شود. اگر کلید دیده شد (در گیت، عکس یا پیام)، فوراً از مدیر سرویس کلید جدید بخواهید.
- **برنامه‌های مرورگر:** کلید را در جاوااسکریپت سمت کاربر نگذارید؛ هر بازدیدکننده می‌تواند آن را ببیند. درخواست را از سرور خودتان بفرستید.

## برای مدیر سرویس: صدور کلید برای کاربران

کلیدها در متغیر `API_KEY` سرور نگه داشته می‌شوند و می‌توان چند کلید را با کاما جدا کرد. به هر کاربر یا برنامه یک کلید جدا بدهید.

1. کلید بسازید:

    ```python
    import secrets
    print(secrets.token_urlsafe(32))
    ```

2. در فایل `.env` کنار برنامه روی هاست، کلید جدید را با کاما به خط `API_KEY` اضافه کنید: `API_KEY=کلید-قدیمی,کلید-کاربر-جدید`
3. برنامه را Restart کنید (در Setup Python App دکمهٔ Restart). کلید جدید بلافاصله کار می‌کند.
4. برای لغو دسترسی یک کاربر، کلید او را از همان خط حذف کنید و Restart بزنید.

اگر `API_KEY` را در بخش Environment variables همان صفحهٔ Setup Python App هم گذاشته‌اید، آن اولویت دارد و تغییر `.env` اثر ندارد. دستور `python setup_and_test.py --rotate` کل خط `API_KEY` را با یک کلید تازه جایگزین می‌کند و بقیهٔ کلیدها از کار می‌افتند؛ فقط وقتی استفاده کنید که می‌خواهید همه را عوض کنید.

</div>
