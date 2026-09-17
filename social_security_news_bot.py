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

# فایل ذخیره‌ی وضعیت کوییز روزانه (آخرین تاریخ ارسال + شماره‌ی سوال بعدی)
QUIZ_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quiz_state.json")

# ساعت ارسال کوییز روزانه، به‌وقت تهران (۹:۳۰ صبح — زمانی که معمولاً
# بازنشستگان و کارگران صبح‌ها گوشی را چک می‌کنند و با ربات‌های ارز/متن
# صبحگاهی‌تان تداخل ندارد)
QUIZ_HOUR_TEHRAN = 9

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


# ---------------------------------------------------------------------------
# کوییز روزانه — بانک سوالات (هر سوال بر اساس منبع رسمی/معتبر تایید شده،
# نه حدس؛ منبع هرکدام در کامنت جلوی آن آمده تا در صورت تغییر قانون در
# سال‌های بعد، به‌روزرسانی‌اش ساده باشد)
# ---------------------------------------------------------------------------
QUIZ_BANK = [
    {
        "category": "قوانین جاری کار",
        "question": "حداقل دستمزد روزانه‌ی کارگران در سال ۱۴۰۵ چند ریال است؟",
        "options": ["۵,۵۴۱,۸۵۰ ریال", "۳,۲۰۰,۰۰۰ ریال", "۷,۰۰۰,۰۰۰ ریال", "۴,۵۰۰,۰۰۰ ریال"],
        "correct_index": 0,
        "explanation": "طبق مصوبه شورای عالی کار (جلسه ۳۴۱، اسفند ۱۴۰۴)، حداقل مزد روزانه ۱۴۰۵ برابر ۵,۵۴۱,۸۵۰ ریال تعیین شد.",
    },
    {
        "category": "قوانین جاری کار",
        "question": "طبق قانون کار ایران، حداکثر ساعت کار عادی در هفته چند ساعت است؟",
        "options": ["۴۰ ساعت", "۴۴ ساعت", "۴۸ ساعت", "۳۶ ساعت"],
        "correct_index": 1,
        "explanation": "بر اساس ماده ۵۱ قانون کار، ساعت کار عادی کارگران حداکثر ۴۴ ساعت در هفته است.",
    },
    {
        "category": "قوانین جاری کار",
        "question": "مرخصی استحقاقی سالانه‌ی کارگران طبق قانون کار چند روز کاری است؟",
        "options": ["۱۲ روز", "۱۸ روز", "۲۶ روز", "۳۰ روز"],
        "correct_index": 2,
        "explanation": "طبق ماده ۶۴ قانون کار، مرخصی استحقاقی سالانه معادل یک ماه (۲۶ روز کاری) است.",
    },
    {
        "category": "چالش‌های بازنشستگان",
        "question": "سن بازنشستگی مردان بیمه‌شده‌ی تأمین اجتماعی طبق قانون جدید بازنشستگی از چند سال به چند سال افزایش یافته؟",
        "options": ["از ۵۵ به ۶۰", "از ۶۰ به ۶۲", "از ۶۲ به ۶۵", "تغییری نکرده"],
        "correct_index": 1,
        "explanation": "طبق قانون جدید بازنشستگی، سن بازنشستگی مردان از ۶۰ به ۶۲ سال افزایش یافته است.",
    },
    {
        "category": "چالش‌های بازنشستگان",
        "question": "«متناسب‌سازی حقوق بازنشستگان» که این روزها زیاد در اخبار می‌آید، در سال ۱۴۰۵ در چه مرحله‌ای قرار دارد؟",
        "options": ["مرحله اول", "مرحله دوم", "مرحله سوم", "هنوز شروع نشده"],
        "correct_index": 2,
        "explanation": "مرحله اول متناسب‌سازی در ۱۴۰۳، مرحله دوم در ۱۴۰۴ و مرحله سوم در سال ۱۴۰۵ در حال اجراست.",
    },
    {
        "category": "چالش‌های بازنشستگان",
        "question": "بر اساس آخرین آمار، پرداخت معوقات فروردین‌ماه بازنشستگان تأمین اجتماعی شامل حال چند نفر می‌شد؟",
        "options": ["حدود ۵۰۰ هزار نفر", "حدود ۵.۳ میلیون نفر", "حدود ۱ میلیون نفر", "حدود ۱۰ میلیون نفر"],
        "correct_index": 1,
        "explanation": "طبق اطلاعیه‌ی سازمان تأمین اجتماعی، این معوقات شامل حال ۵ میلیون و ۳۰۰ هزار بازنشسته و مستمری‌بگیر بود.",
    },
    {
        "category": "سلامت و بهداشت",
        "question": "طرحی که این روزها تأمین اجتماعی برای هدایت بیمه‌شدگان به سمت یک پزشک مشخص پیش از مراجعه به متخصص اجرا می‌کند، چه نام دارد؟",
        "options": ["نظام ارجاع و پزشک خانواده", "طرح تحول سلامت", "بیمه تکمیلی رایگان", "کارت هوشمند درمان"],
        "correct_index": 0,
        "explanation": "معاونت درمان تأمین اجتماعی در حال اجرای برنامه‌ی «پزشکی خانواده و نظام ارجاع» برای بیمه‌شدگان است.",
    },
    {
        "category": "سلامت و بهداشت",
        "question": "کدام مورد جزو خدمات درمانی رایج تحت پوشش تأمین اجتماعی برای بازنشستگان است؟",
        "options": ["بیمه بدنه خودرو", "فیش دارویی و بیمه تکمیلی درمان", "بیمه مسافرتی خارج از کشور", "بیمه عمر و سرمایه‌گذاری"],
        "correct_index": 1,
        "explanation": "فیش دارویی و بیمه تکمیلی درمان از خدمات اصلی حوزه‌ی سلامت تأمین اجتماعی برای بازنشستگان است.",
    },
    {
        "category": "قوانین جاری کار",
        "question": "بر اساس بخشنامه‌ی ۱۴۰۵، حق اولاد ماهانه برای هر فرزند کارگران چقدر تعیین شد؟",
        "options": ["حدود ۵۰۰ هزار تومان", "حدود ۱.۶ میلیون تومان", "حدود ۳ میلیون تومان", "پرداخت نمی‌شود"],
        "correct_index": 1,
        "explanation": "طبق بخشنامه‌ی ۱۴۰۵، حق اولاد هر فرزند ماهانه ۱۶,۶۲۵,۵۵۰ ریال (حدود ۱.۶ میلیون تومان) تعیین شد.",
    },
]


def load_quiz_state():
    default = {"last_quiz_date": "", "quiz_index": 0}
    if os.path.exists(QUIZ_STATE_FILE):
        try:
            with open(QUIZ_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            default.update(data)
        except Exception:
            pass
    return default


def save_quiz_state(state):
    with open(QUIZ_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)


def send_quiz_to_telegram(quiz_item):
    """ارسال یک سوال کوییز با استفاده از متد رسمی sendPoll در Bot API تلگرام
    (type='quiz')؛ خودِ تلگرام رأی‌گیری، درصدها و نمایش جواب درست را انجام
    می‌دهد، هیچ زیرساخت اضافه‌ای لازم نیست."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPoll"
    prefixed_question = f"🧠 کوییز روزانه صبا رسانه | {quiz_item['category']}\n\n{quiz_item['question']}"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "question": prefixed_question[:300],
        "options": json.dumps(quiz_item["options"], ensure_ascii=False),
        "type": "quiz",
        "correct_option_id": quiz_item["correct_index"],
        "is_anonymous": "true",
        "explanation": quiz_item["explanation"][:200],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("ok"):
            return True
        print(f"[warn] ارسال کوییز به تلگرام رد شد: {result}")
        return False
    except Exception as e:
        print(f"[warn] ارسال کوییز به تلگرام با خطا مواجه شد: {e}")
        return False


def maybe_send_daily_quiz():
    """اگر ساعت فعلی به‌وقت تهران برابر QUIZ_HOUR_TEHRAN باشد و امروز هنوز
    کوییزی ارسال نشده باشد، یک سوال (به‌ترتیب چرخشی از QUIZ_BANK) می‌فرستد."""
    try:
        from zoneinfo import ZoneInfo
        tehran_now = datetime.now(ZoneInfo("Asia/Tehran"))
    except Exception:
        tehran_now = datetime.now(timezone.utc) + timedelta(hours=3, minutes=30)

    if tehran_now.hour != QUIZ_HOUR_TEHRAN:
        return

    state = load_quiz_state()
    today_str = tehran_now.strftime("%Y-%m-%d")
    if state.get("last_quiz_date") == today_str:
        return  # امروز قبلاً کوییز ارسال شده

    idx = state.get("quiz_index", 0) % len(QUIZ_BANK)
    quiz_item = QUIZ_BANK[idx]
    print(f"[debug] ارسال کوییز روزانه، موضوع: {quiz_item['category']}")
    if send_quiz_to_telegram(quiz_item):
        state["last_quiz_date"] = today_str
        state["quiz_index"] = idx + 1
        save_quiz_state(state)


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


# اگر لینک خودش به این اندازه یا کوتاه‌تر باشد، نیازی به کوتاه‌کردن نیست
# (خیلی از خبرگزاری‌ها مثل ایلنا خودشان لینک کوتاه با شناسه‌ی عددی می‌دهند،
# مثل https://www.ilna.ir/fa/tiny/news-1839818 — نیازی به سرویس بیرونی نیست)
SHORT_LINK_MAX_LEN = 60


def shorten_link(url):
    """اگر لینک خودِ خبرگزاری از قبل کوتاه است، همان استفاده می‌شود (بدون هیچ
    وابستگی به سرویس بیرونی). فقط برای لینک‌های واقعاً بلند (مثلاً اسلاگ فارسی
    طولانی) سراغ زنجیره‌ای از سرویس‌های رایگان کوتاه‌کننده می‌رویم؛ اگر همه‌ی
    آن‌ها هم شکست بخورند، خودِ لینک اصلی (کامل) برگردانده می‌شود."""
    if len(url) <= SHORT_LINK_MAX_LEN or "/tiny/" in url:
        return url

    services = [
        ("TinyURL", "https://tinyurl.com/api-create.php?" + urllib.parse.urlencode({"url": url})),
        ("is.gd", "https://is.gd/create.php?" + urllib.parse.urlencode({"format": "simple", "url": url})),
        ("da.gd", "https://da.gd/shorten?" + urllib.parse.urlencode({"url": url})),
    ]
    for name, api in services:
        try:
            req = urllib.request.Request(api, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as resp:
                short = resp.read().decode("utf-8").strip()
            if short.startswith("http") and len(short) < len(url):
                return short
            print(f"[warn] سرویس کوتاه‌کننده {name} پاسخ نامعتبر داد: {short[:150]}")
        except Exception as e:
            print(f"[warn] سرویس کوتاه‌کننده {name} شکست خورد: {e}")
    print("[warn] هر سه سرویس کوتاه‌کننده‌ی لینک شکست خوردند؛ لینک کامل ارسال می‌شود.")
    return url


# نام ماه‌های شمسی، برای نمایش تاریخ به‌شکل آشنا برای مخاطب فارسی‌زبان
_JALALI_MONTHS = [
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
]
_PERSIAN_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def _gregorian_to_jalali(gy, gm, gd):
    """تبدیل تاریخ میلادی به شمسی (الگوریتم استاندارد و متن‌باز، بدون نیاز به کتابخانه‌ی جانبی)."""
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + (days % 31)
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + ((days - 186) % 30)
    return jy, jm, jd


def format_persian_datetime(pub_date_str):
    """تبدیل تاریخ RSS (میلادی/GMT) به شمسی و ساعت تهران، با اعداد فارسی."""
    if not pub_date_str:
        return ""
    try:
        dt = parsedate_to_datetime(pub_date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        try:
            from zoneinfo import ZoneInfo
            tehran = dt.astimezone(ZoneInfo("Asia/Tehran"))
        except Exception:
            # اگر دیتابیس منطقه‌ی زمانی روی رانر موجود نبود، آفست ثابت +۳:۳۰ را دستی اعمال می‌کنیم
            tehran = dt.astimezone(timezone(timedelta(hours=3, minutes=30)))
        jy, jm, jd = _gregorian_to_jalali(tehran.year, tehran.month, tehran.day)
        text = f"{jd} {_JALALI_MONTHS[jm - 1]} {jy} - ساعت {tehran.strftime('%H:%M')}"
        return text.translate(_PERSIAN_DIGITS)
    except Exception:
        return pub_date_str  # در صورت هر خطای غیرمنتظره، حداقل تاریخ خام نمایش داده شود


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


# فضای‌نام استاندارد Media RSS (برای <media:content> و <media:thumbnail>)
MEDIA_RSS_NS = "{http://search.yahoo.com/mrss/}"


def extract_image(item_el, desc_raw_html):
    """پیدا کردن تصویر خبر با پوشش هر سه روش رایج در RSS:
    ۱) <enclosure url="..." type="image/...">   (استاندارد RSS 2.0)
    ۲) <media:content url="..." medium="image"> یا <media:thumbnail url="...">
    ۳) یک تگ <img src="..."> داخل خودِ description
    هر روشی که اول جواب بدهد استفاده می‌شود؛ اگر هیچ‌کدام نبود None برمی‌گردد
    (یعنی این خبر تصویر ندارد و باید به‌صورت متنی معمولی ارسال شود)."""
    enclosure = item_el.find("enclosure")
    if enclosure is not None:
        url = enclosure.get("url")
        type_ = enclosure.get("type", "")
        if url and (not type_ or type_.startswith("image")):
            return url

    media_content = item_el.find(f"{MEDIA_RSS_NS}content")
    if media_content is not None:
        url = media_content.get("url")
        medium = media_content.get("medium", "")
        type_ = media_content.get("type", "")
        if url and (medium == "image" or type_.startswith("image") or not medium):
            return url

    media_thumb = item_el.find(f"{MEDIA_RSS_NS}thumbnail")
    if media_thumb is not None and media_thumb.get("url"):
        return media_thumb.get("url")

    if desc_raw_html:
        m = re.search(r'<img[^>]+src="([^"]+)"', desc_raw_html)
        if m:
            return m.group(1)

    return None


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

        image_url = extract_image(item, desc_raw)
        desc = html.unescape(re.sub("<[^<]+?>", "", desc_raw)).strip()

        items.append({
            "title": title.strip(),
            "source": (source or "نامشخص").strip(),
            "date": pub_date,
            "summary_raw": desc,
            "link": link,
            "image": image_url,
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
    """خلاصه‌ی استخراجی ساده وقتی کلید LLM موجود نیست (بدون هیچ سرویس پولی).
    عمداً تیتر را در متن تکرار نمی‌کند (چون تیتر جداگانه در پیام نمایش داده می‌شود).
    نکته‌ی صادقانه: بدون کلید Groq، ممکن است این خلاصه به ۸۰ کلمه نرسد، چون
    فید RSS خبرگزاری‌ها معمولاً فقط یک یا دو جمله‌ی کوتاه (لید خبر) می‌دهد،
    نه متن کامل. برای خلاصه‌ی تضمینی ۸۰-۱۲۰ کلمه‌ای، تنظیم GROQ_API_KEY لازم است."""
    base = (item["summary_raw"] or "").strip()
    if not base:
        return "خلاصه‌ای از منبع در دسترس نبود؛ برای جزئیات کامل به لینک خبر مراجعه کنید."
    words = base.split()
    summary = " ".join(words[:120])
    if len(words) < 80:
        summary += " (برای جزئیات بیشتر به لینک خبر مراجعه کنید.)"
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
    """ارسال پیام متنی معمولی (بدون عکس)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("ok"):
            return True
        print(f"[warn] ارسال پیام متنی به تلگرام رد شد: {result}")
        return False
    except Exception as e:
        print(f"[warn] ارسال به تلگرام ناموفق بود: {e}")
        return False


def send_photo_to_telegram(image_url, caption_html):
    """ارسال عکس با کپشن. اگر تلگرام نتواند لینک عکس را دریافت کند (لینک
    خراب، غیرقابل‌دسترس، فرمت پشتیبانی‌نشده و ...)، False برمی‌گرداند تا
    فراخوان بتواند بدون از دست‌دادن خبر، به ارسال متنی معمولی برگردد."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "photo": image_url,
        "caption": caption_html,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if result.get("ok"):
            return True
        print(f"[warn] ارسال عکس به تلگرام رد شد: {result}")
        return False
    except Exception as e:
        print(f"[warn] ارسال عکس به تلگرام با خطا مواجه شد: {e}")
        return False


# حداکثر طول مجاز کپشن تلگرام برای پیام‌های همراه با عکس (محدودیت خودِ API تلگرام)
TELEGRAM_CAPTION_LIMIT = 1024


def build_caption_html(item):
    """کپشن مخصوص حالت عکس‌دار: فقط تیتر + خلاصه + لینک. منبع/تاریخ/امضا
    عمداً اینجا نیستند چون در یک پیام کوتاه جداگانه‌ی بعد از عکس می‌آیند —
    این‌طوری معمولاً کاملاً زیر ۱۰۲۴ کاراکتر می‌ماند. اگر با این حال زیاد
    بود، فقط بخشی از خلاصه (نه تیتر، نه لینک) کوتاه می‌شود."""
    title = html.escape(item["title"])
    summary = html.escape(item["summary"])
    short_link = shorten_link(item["link"])

    def build(s):
        return f"📰 <b>{title}</b>\n\n{s}\n\n🔗 {short_link}"

    caption = build(summary)
    if len(caption) > TELEGRAM_CAPTION_LIMIT:
        overflow = len(caption) - TELEGRAM_CAPTION_LIMIT + 1  # +1 برای «…»
        trimmed = summary[: max(0, len(summary) - overflow)] + "…"
        caption = build(trimmed)
    return caption[:TELEGRAM_CAPTION_LIMIT]


def build_footer_html(item):
    """پیام کوتاه بعد از عکس: منبع، تاریخ، و امضای صبا رسانه."""
    source = html.escape(item["source"])
    persian_date = format_persian_datetime(item.get("date", ""))
    return (
        f"🗞 منبع: {source}\n"
        f"🕒 تاریخ: {persian_date}\n"
        f"—————————————\n"
        f"📡 صبا رسانه\n"
        f"🆔 {SABA_ID}\n"
        f"🔗 {SABA_LINK}"
    )


def format_telegram_message(item):
    """پیام کامل و شکیل برای حالت بدون عکس؛ از HTML parse mode تلگرام استفاده می‌کند.
    ترتیب: تیتر (بولد) → خلاصه → منبع و تاریخ → لینک کوتاه → امضای صبا رسانه."""
    title = html.escape(item["title"])
    summary = html.escape(item["summary"])
    source = html.escape(item["source"])
    persian_date = format_persian_datetime(item.get("date", ""))
    short_link = shorten_link(item["link"])

    return (
        f"📰 <b>{title}</b>\n\n"
        f"{summary}\n\n"
        f"—————————————\n"
        f"🗞 منبع: {source}\n"
        f"🕒 تاریخ: {persian_date}\n"
        f"🔗 {short_link}\n"
        f"—————————————\n"
        f"📡 صبا رسانه\n"
        f"🆔 {SABA_ID}\n"
        f"🔗 {SABA_LINK}"
    )


def send_news_item(item):
    """مسیر ارسال یک خبر: اگر عکس دارد، عکس+کپشن و بعد یک پیام کوتاه امضا
    ارسال می‌شود؛ اگر عکس ندارد یا ارسال عکس با هر دلیلی شکست بخورد
    (لینک خراب، تایم‌اوت، فرمت نامعتبر و ...)، بدون از دست‌رفتن خبر،
    به ارسال متنیِ کاملِ معمولی برمی‌گردیم."""
    image_url = item.get("image")
    if image_url:
        caption = build_caption_html(item)
        if send_photo_to_telegram(image_url, caption):
            send_to_telegram(build_footer_html(item))
            return
        print("[warn] ارسال عکس شکست خورد؛ به‌جای آن پیام متنی کامل ارسال می‌شود.")
    send_to_telegram(format_telegram_message(item))




# ---------------------------------------------------------------------------
# اجرای اصلی
# ---------------------------------------------------------------------------

def _process_feed(label, raw, seen, new_items, default_source=None):
    """پارس یک پاسخ RSS، فیلتر بر اساس ارتباط موضوعی، و افزودن آیتم‌های جدید. لاگ تشخیصی چاپ می‌کند."""
    # برخی سرورها (مثل ایلنا) قبل از "<?xml ...?>" یک فاصله/نیولاین اضافه می‌فرستند
    # که طبق استاندارد XML غیرمجاز است؛ حذفش می‌کنیم تا پارس شکست نخورد.
    raw = raw.lstrip()
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
            "image": it.get("image"),
        })

    save_seen(seen)

    if not result_items:
        output = "خبر جدیدی یافت نشد."
        print(output)
    else:
        output_json = json.dumps({"items": result_items}, ensure_ascii=False, indent=2)
        print(output_json)

        # ارسال هر خبر: با عکس (در صورت وجود) یا به‌صورت متنی
        for it in result_items:
            send_news_item(it)

    # کوییز روزانه مستقل از وجود یا نبود خبر تازه بررسی و (در ساعت مقرر) ارسال می‌شود
    maybe_send_daily_quiz()


if __name__ == "__main__":
    main()
