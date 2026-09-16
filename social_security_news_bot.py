#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ربات خبری «صبا رسانه» — گردآوری و خلاصه‌سازی اخبار تأمین اجتماعی ایران
=======================================================================
این اسکریپت به‌صورت رایگان (بدون نیاز به API خبری پولی) اجرا می‌شود:
  - منبع اخبار: Google News RSS (رایگان، بدون کلید API)
  - خلاصه‌سازی: در صورت تنظیم کلید رایگان Groq (console.groq.com) از آن
    استفاده می‌کند؛ در غیر این صورت از خلاصه‌ی خودِ فید (extractive) استفاده
    می‌کند تا اسکریپت بدون هیچ کلیدی هم کار کند.
  - ارسال: اختیاری، به ربات تلگرام (رایگان) از طریق Bot API.

نحوه‌ی اجرای زمان‌بندی‌شده (هر ۳۰ دقیقه) با cron در لینوکس:
    */30 * * * * /usr/bin/python3 /path/to/social_security_news_bot.py >> /path/to/log.txt 2>&1

نکته‌ی فیلترینگ در ایران:
    اگر این اسکریپت را روی سروری داخل ایران اجرا می‌کنید، دسترسی به
    news.google.com و api.groq.com و api.telegram.org ممکن است نیاز به
    پراکسی/VPN داشته باشد. ساده‌ترین راه رایگان: اجرای اسکریپت روی یک
    سرور خارج از ایران (مثلاً یک VPS رایگان/ارزان یا GitHub Actions
    scheduled workflow که رایگان است) و فقط ارسال نتیجه به تلگرام.
"""

import os
import re
import json
import html
import hashlib
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone, timedelta

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

# موضوعات مورد نظر برای جستجو در Google News (فارسی، محدود به منابع ایرانی)
# نکته: عمداً کوتاه و تک/دوکلمه‌ای هستند. گوگل کلمات را با AND ترکیب می‌کند،
# پس عبارت‌های طولانی (۳-۴ کلمه‌ای) عملاً هیچ نتیجه‌ای برنمی‌گردانند.
# دقتِ از دست‌رفته‌ی این جست‌وجوی گسترده را تابع is_relevant() در ادامه جبران می‌کند.
#
# هشدار شناخته‌شده: گوگل‌نیوز از IPهای دیتاسنتری (مثل رانرهای GitHub Actions)
# گاهی یک فید معتبر ولی کاملاً خالی برمی‌گرداند (نوعی مسدودسازی ضدِ اسکرپینگ،
# نه خطای HTTP). به همین دلیل، منابع مستقیمِ خبرگزاری‌ها (پایین‌تر) منبع
# اصلی و قابل‌اتکاتر محسوب می‌شوند؛ گوگل‌نیوز فقط به‌عنوان پوشش تکمیلی است.
GOOGLE_TOPICS = [
    "تامین اجتماعی",
    "بازنشستگان",
    "مستمری بگیران",
    "شستا",
    "بیمه شدگان",
    "قانون کار",
]

# بازه‌ی زمانی جست‌وجو در Google News (این منبع فعلاً تکمیلی/غیرفعال است، رجوع کنید به یادداشت بالا).
SEARCH_WINDOW = "1d"

# حداکثر سن مجاز خبر بر حسب ساعت. صرف‌نظر از تکراری‌بودن یا نبودن، هر خبری
# که از تاریخ انتشارش بیشتر از این مقدار گذشته باشد نادیده گرفته می‌شود.
# این تضمین می‌کند حتی اگر اجرای خودکار چند روز متوقف شده باشد، فقط اخبار
# واقعاً تازه (نه انباشته‌شده‌ی چندروزه) ارسال شود.
MAX_NEWS_AGE_HOURS = 24

# فیدهای RSS مستقیمِ خبرگزاری‌های ایرانی (منبع اصلی و قابل‌اتکا).
# این‌ها فیدهای عمومیِ هر خبرگزاری‌اند؛ فیلتر is_relevant() در ادامه
# فقط خبرهای مرتبط با تأمین اجتماعی/بازنشستگی/کار را از میان آن‌ها جدا می‌کند.
# نکته: دامنه‌ی tasnimnews.com توسط آمریکا مسدود شده؛ تسنیم به دامنه‌ی .ir منتقل شده.
# فارس هم فعلاً فید ساده‌ی XML ندارد (نیازمند جاوااسکریپت) و ناپایدار است،
# به همین دلیل با خبرآنلاین جایگزین شده.
_TASNIM_PATH = urllib.parse.quote("مهمترین-اخبار-تسنیم")
DIRECT_RSS_FEEDS = [
    ("ایلنا (کار و تأمین اجتماعی)", "https://www.ilna.ir/rss"),
    ("ایسنا", "https://www.isna.ir/rss"),
    ("ایرنا", "https://www.irna.ir/rss"),
    ("مهر", "https://www.mehrnews.com/rss"),
    ("تسنیم", f"https://www.tasnimnews.ir/fa/rss/feed/0/8/0/{_TASNIM_PATH}"),
    ("خبرآنلاین", "https://www.khabaronline.ir/rss"),
]

# هویت کانال صبا رسانه، برای درج در پایان هر خبر
SABA_ID = "@saba_rasanehh"
SABA_LINK = "https://t.me/saba_rasanehh"
SABA_CHANNEL_LINE = f"کانال صبا رسانه: {SABA_ID} ({SABA_LINK})"

# فایل ذخیره‌ی لینک‌های قبلاً ارسال‌شده (برای جلوگیری از تکرار خبر)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_links.json")

# کلید رایگان Groq برای خلاصه‌سازی با LLM (اختیاری). اگر خالی باشد،
# اسکریپت به خلاصه‌ی استخراجی سبک بسنده می‌کند.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.1-8b-instant"  # مدل رایگان و سریع Groq

# تلگرام (اختیاری) — برای ارسال خودکار خروجی به یک گروه/کانال
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# کلمات کلیدی که باید در عنوان/خلاصه باشد تا خبر «مرتبط» تلقی شود.
# «کارگر» عمداً با (?!دان) همراه شده تا با کلماتی مثل «کارگردان»/«کارگردانی»
# (که تصادفاً همین حروف را در خود دارند) اشتباه گرفته نشود.
RELEVANT_PATTERNS = [
    re.compile(p) for p in [
        "تامین اجتماعی", "تأمین اجتماعی", "بازنشسته", "بازنشستگان", "مستمری",
        r"کارگر(?!دان)", "بیمه شده", "بیمه\u200cشده", "همسان سازی", "همسان\u200cسازی",
        "معیشت", "شستا", "قانون کار",
    ]
]

# عبارات تبلیغاتی/نامرتبط که باید حذف شوند
BLOCK_KEYWORDS = ["تخفیف ویژه", "آگهی", "تبلیغ", "فروش ویژه"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


# ---------------------------------------------------------------------------
# توابع کمکی
# ---------------------------------------------------------------------------

def load_seen():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def save_seen(seen):
    # فقط ۵۰۰۰ لینک آخر را نگه می‌داریم تا فایل بزرگ نشود
    trimmed = list(seen)[-5000:]
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False)


def is_too_old(pub_date_str):
    """بررسی می‌کند آیا خبر از MAX_NEWS_AGE_HOURS قدیمی‌تر است یا نه.
    اگر تاریخ قابل‌تفسیر نبود، برای احتیاط خبر را قدیمی در نظر نمی‌گیریم
    (یعنی اجازه می‌دهیم رد شود، چون بهتر است یک خبر مشکوک نمایش داده شود
    تا اینکه به‌خاطر یک تاریخ ناقص، کل خبر گم شود)."""
    if not pub_date_str:
        return False
    try:
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        return age > timedelta(hours=MAX_NEWS_AGE_HOURS)
    except Exception:
        return False


def shorten_link(url):
    """کوتاه‌کردن لینک با سرویس رایگان TinyURL (بدون نیاز به کلید/ثبت‌نام).
    در صورت هر خطایی (قطعی شبکه و ...) خودِ لینک اصلی برگردانده می‌شود."""
    try:
        api = "https://tinyurl.com/api-create.php?" + urllib.parse.urlencode({"url": url})
        req = urllib.request.Request(api, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=10) as resp:
            short = resp.read().decode("utf-8").strip()
        if short.startswith("http"):
            return short
    except Exception:
        pass
    return url


def fetch_google_rss(query):
    """گرفتن فید RSS رایگان Google News برای یک عبارت جستجو، محدود به منابع فارسی/ایران."""
    params = {
        "q": f"{query} when:{SEARCH_WINDOW}",
        "hl": "fa",
        "gl": "IR",
        "ceid": "IR:fa",
    }
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    return data


def fetch_direct_rss(url):
    """گرفتن فید RSS عمومی یک خبرگزاری، بدون هیچ پارامتر جست‌وجو."""
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    return data


def parse_items(xml_root, default_source=None):
    items = []
    for item in xml_root.findall(".//item"):
        title_raw = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        desc_raw = (item.findtext("description") or "")
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None and source_el.text else None

        # قالب معمول Google News: "عنوان خبر - نام رسانه"
        title = title_raw
        if not source and " - " in title_raw:
            title, source = title_raw.rsplit(" - ", 1)

        # برای فیدهای مستقیم خبرگزاری‌ها، نام منبع را از قبل می‌دانیم
        if not source and default_source:
            source = default_source

        desc = html.unescape(re.sub("<[^<]+?>", "", desc_raw)).strip()

        items.append({
            "title": title.strip(),
            "source": (source or "نامشخص").strip(),
            "date": pub_date,
            "summary_raw": desc,
            "link": link,
        })
    return items


def is_relevant(item):
    text = f"{item['title']} {item['summary_raw']}"
    if any(b in text for b in BLOCK_KEYWORDS):
        return False
    return any(p.search(text) for p in RELEVANT_PATTERNS)


def dedupe_key(item):
    # هش لینک برای جلوگیری از ارسال خبر تکراری
    return hashlib.sha256(item["link"].encode("utf-8")).hexdigest()


def fallback_summary(item):
    """خلاصه‌ی استخراجی ساده وقتی کلید LLM موجود نیست (بدون هیچ سرویس پولی)."""
    base = item["summary_raw"] or item["title"]
    words = base.split()
    if len(words) < 40:
        # اگر خلاصه‌ی فید کوتاه است، عنوان را هم اضافه می‌کنیم تا به حداقل کلمات نزدیک شود
        base = f"{item['title']}. {base}"
        words = base.split()
    summary = " ".join(words[:120])
    if len(words) < 80:
        summary += " (برای جزئیات کامل به لینک خبر مراجعه کنید.)"
    return summary


def llm_summary(item):
    """خلاصه‌سازی دقیق‌تر با مدل رایگان Groq (در صورت تنظیم GROQ_API_KEY)."""
    if not GROQ_API_KEY:
        return fallback_summary(item)
    try:
        prompt = (
            "یک خلاصه‌ی دقیق، بی‌طرفانه و خبری بین ۸۰ تا ۱۲۰ کلمه به زبان فارسی "
            "برای این خبر بنویس. فقط خلاصه را برگردان، بدون مقدمه:\n\n"
            f"عنوان: {item['title']}\nمتن/چکیده موجود: {item['summary_raw']}"
        )
        body = json.dumps({
            "model": GROQ_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 300,
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        # اگر سرویس رایگان در دسترس نبود (مثلاً به‌خاطر فیلترینگ)، به روش ساده برمی‌گردیم
        return fallback_summary(item)


def send_to_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"[warn] ارسال به تلگرام ناموفق بود: {e}")


def format_telegram_message(item):
    """پیام شکیل و حرفه‌ای برای تلگرام؛ از HTML parse mode تلگرام استفاده می‌کند."""
    title = html.escape(item["title"])
    summary = html.escape(item["summary"])
    source = html.escape(item["source"])
    short_link = shorten_link(item["link"])

    return (
        f"📰 <b>{title}</b>\n\n"
        f"{summary}\n\n"
        f"🗞 منبع: {source}\n"
        f"🔗 {short_link}\n"
        f"—————————————\n"
        f"📡 صبا رسانه\n"
        f"🆔 {SABA_ID}\n"
        f"🔗 {SABA_LINK}"
    )


# ---------------------------------------------------------------------------
# اجرای اصلی
# ---------------------------------------------------------------------------

def _process_feed(label, raw, seen, new_items, default_source=None):
    """پارس یک پاسخ RSS، فیلتر بر اساس ارتباط موضوعی، و افزودن آیتم‌های جدید. لاگ تشخیصی چاپ می‌کند."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        # اگر منبع به‌جای RSS یک صفحه‌ی HTML (مثلاً کپچا/خطا) برگردانده باشد
        print(f"[warn] پاسخ نامعتبر (غیر XML) برای «{label}»: {e}")
        print("[debug] نمونه‌ی پاسخ:", raw[:200])
        return

    raw_items = parse_items(root, default_source=default_source)
    print(f"[debug] «{label}»: {len(raw_items)} آیتم خام دریافت شد")
    if not raw_items:
        # کمک به عیب‌یابی: نشان می‌دهد آیا واقعاً فید خالی بوده یا چیز غیرمنتظره برگشته
        print("[debug] نمونه‌ی پاسخ خام:", raw[:200])

    kept = 0
    too_old = 0
    for raw_item in raw_items:
        if not raw_item["link"] or not is_relevant(raw_item):
            continue
        if is_too_old(raw_item["date"]):
            too_old += 1
            continue
        key = dedupe_key(raw_item)
        if key in seen:
            continue
        seen.add(key)
        new_items.append(raw_item)
        kept += 1
    print(f"[debug] «{label}»: {kept} خبر جدید و مرتبط بعد از فیلتر (و {too_old} خبر قدیمی‌تر از {MAX_NEWS_AGE_HOURS} ساعت کنار گذاشته شد)")


def main():
    seen = load_seen()
    new_items = []

    # منبع اصلی: فیدهای مستقیم خبرگزاری‌های ایرانی (بدون محدودیت ضدِ اسکرپینگ گوگل)
    for name, feed_url in DIRECT_RSS_FEEDS:
        try:
            raw = fetch_direct_rss(feed_url)
        except Exception as e:
            print(f"[warn] خطا در دریافت فید «{name}» ({feed_url}): {e}")
            continue
        _process_feed(name, raw, seen, new_items, default_source=name)

    # منبع تکمیلی: جست‌وجوی گوگل‌نیوز (ممکن است روی برخی سرورها خالی برگردد)
    for topic in GOOGLE_TOPICS:
        try:
            raw = fetch_google_rss(topic)
        except Exception as e:
            print(f"[warn] خطا در دریافت Google News برای «{topic}»: {e}")
            continue
        _process_feed(f"Google: {topic}", raw, seen, new_items)

    result_items = []
    for it in new_items:
        result_items.append({
            "title": it["title"],
            "source": it["source"],
            "date": it["date"],
            "summary": llm_summary(it),
            "link": it["link"],
            "channel": SABA_CHANNEL_LINE,
        })

    save_seen(seen)

    if not result_items:
        output = "خبر جدیدی یافت نشد."
        print(output)
        return

    output_json = json.dumps({"items": result_items}, ensure_ascii=False, indent=2)
    print(output_json)

    # آماده‌سازی و ارسال پیام شکیل به تلگرام
    for it in result_items:
        msg = format_telegram_message(it)
        send_to_telegram(msg)


if __name__ == "__main__":
    main()
