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
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

# موضوعات مورد نظر برای جستجو در Google News (فارسی، محدود به منابع ایرانی)
# نکته: عمداً کوتاه و تک/دوکلمه‌ای هستند. گوگل کلمات را با AND ترکیب می‌کند،
# پس عبارت‌های طولانی (۳-۴ کلمه‌ای) عملاً هیچ نتیجه‌ای برنمی‌گردانند.
# دقتِ از دست‌رفته‌ی این جست‌وجوی گسترده را تابع is_relevant() در ادامه جبران می‌کند.
TOPICS = [
    "تامین اجتماعی",
    "بازنشستگان",
    "مستمری بگیران",
    "شستا",
    "بیمه شدگان",
    "قانون کار",
]

# بازه‌ی زمانی جست‌وجو در Google News. عبارت‌های تک‌کلمه‌ای بالا معمولاً
# در یک روز هم نتیجه دارند، اما برای اطمینان بیشتر ۳ روز گذاشته شده؛
# دوباره ارسال‌نشدنِ خبر تکراری را dedupe بر اساس لینک تضمین می‌کند.
SEARCH_WINDOW = "3d"

# کانال صبا رسانه — لینک واقعی کانال را اینجا جایگزین کنید
SABA_CHANNEL_LINE = "کانال صبا رسانه: [لینک کانال]"

# فایل ذخیره‌ی لینک‌های قبلاً ارسال‌شده (برای جلوگیری از تکرار خبر)
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_links.json")

# کلید رایگان Groq برای خلاصه‌سازی با LLM (اختیاری). اگر خالی باشد،
# اسکریپت به خلاصه‌ی استخراجی سبک بسنده می‌کند.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.1-8b-instant"  # مدل رایگان و سریع Groq

# تلگرام (اختیاری) — برای ارسال خودکار خروجی به یک گروه/کانال
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# کلمات کلیدی که باید در عنوان/خلاصه باشد تا خبر «مرتبط» تلقی شود
RELEVANT_KEYWORDS = [
    "تامین اجتماعی", "تأمین اجتماعی", "بازنشسته", "بازنشستگان", "مستمری",
    "کارگر", "کارگران", "بیمه شده", "بیمه‌شده", "همسان سازی", "همسان‌سازی",
    "معیشت", "شستا", "قانون کار",
]

# عبارات تبلیغاتی/نامرتبط که باید حذف شوند
BLOCK_KEYWORDS = ["تخفیف ویژه", "آگهی", "تبلیغ", "فروش ویژه"]

HEADERS = {"User-Agent": "Mozilla/5.0 (SabaMediaBot/1.0; +free-open-source)"}


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


def fetch_rss(query):
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


def parse_items(xml_root):
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
    return any(k in text for k in RELEVANT_KEYWORDS)


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
        "disable_web_page_preview": "false",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        urllib.request.urlopen(req, timeout=20)
    except Exception as e:
        print(f"[warn] ارسال به تلگرام ناموفق بود: {e}")


# ---------------------------------------------------------------------------
# اجرای اصلی
# ---------------------------------------------------------------------------

def main():
    seen = load_seen()
    new_items = []

    for topic in TOPICS:
        try:
            raw = fetch_rss(topic)
        except Exception as e:
            print(f"[warn] خطا در دریافت RSS برای «{topic}»: {e}")
            continue

        try:
            root = ET.fromstring(raw)
        except ET.ParseError as e:
            # اگر گوگل به‌جای RSS یک صفحه‌ی HTML (مثلاً کپچا) برگردانده باشد
            print(f"[warn] پاسخ نامعتبر (غیر XML) برای «{topic}»: {e}")
            print("[debug] نمونه‌ی پاسخ:", raw[:200])
            continue

        raw_items = parse_items(root)
        print(f"[debug] «{topic}»: {len(raw_items)} آیتم خام دریافت شد")

        kept = 0
        for raw_item in raw_items:
            if not raw_item["link"] or not is_relevant(raw_item):
                continue
            key = dedupe_key(raw_item)
            if key in seen:
                continue
            seen.add(key)
            new_items.append(raw_item)
            kept += 1
        print(f"[debug] «{topic}»: {kept} خبر جدید و مرتبط بعد از فیلتر")

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

    # آماده‌سازی پیام متنی برای تلگرام (اختیاری)
    for it in result_items:
        msg = (
            f"📰 {it['title']}\n"
            f"منبع: {it['source']} | تاریخ: {it['date']}\n\n"
            f"{it['summary']}\n\n"
            f"لینک: {it['link']}\n"
            f"{it['channel']}"
        )
        send_to_telegram(msg)


if __name__ == "__main__":
    main()
