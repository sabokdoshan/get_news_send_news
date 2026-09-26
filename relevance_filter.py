# -*- coding: utf-8 -*-
"""
ماژول فیلتر هوشمند اخبار — صبا رسانه
هدف: از میان همه اخبار جمع‌آوری‌شده، فقط مواردی عبور کنند که اثر واقعی و ملموس
روی معیشت (حقوق/دستمزد/مستمری/قدرت خرید) یا درمان (بیمه/دارو/هزینه درمان)
میلیون‌ها کارگر و بازنشسته سراسر کشور دارند — نه اخبار محلی، تشریفاتی یا مختص گروه خاص.

معماری دو‌مرحله‌ای (برای کمینه‌کردن مصرف API رایگان):
  ۱) پیش‌فیلتر کلیدواژه‌ای رایگان و آفلاین → رد قطعی موارد بدیهیِ نامرتبط
  ۲) داوری نهایی با مدل زبانی رایگان (Gemini Flash) برای مواردی که نیاز به تشخیص
     دقیق‌تر دارند (مثلاً خبری که کلمه «کارگر» دارد ولی محتوایش محلی/تشریفاتی است)

نحوه استفاده در اسکریپت فعلی شما:
    from relevance_filter import filter_relevant_news
    items = fetch_all_sources()          # کد فعلی خودتان برای گرفتن اخبار خام
    items = filter_relevant_news(items)  # فقط همین یک خط اضافه می‌شود
    # ادامه روند خلاصه‌سازی و ارسال به تلگرام مثل قبل

نکته: چون این اسکریپت روی GitHub Actions اجرا می‌شود (نه روی سرور داخل ایران)،
فیلترینگ ایران مانع دسترسی به Gemini نمی‌شود.
"""

import os
import json
import requests

# ---------------------------------------------------------------------------
# مرحله ۱: پیش‌فیلتر کلیدواژه‌ای (رایگان، بدون تماس شبکه‌ای)
# ---------------------------------------------------------------------------

# اگر یکی از این‌ها بود و هیچ نشانه قوی مرتبط نبود → رد قطعی، بدون تماس با API
HARD_EXCLUDE = [
    "استاندار", "فرماندار", "بخشدار", "شهردار", "دهیار",
    "افتتاح", "کلنگ‌زنی", "بازدید میدانی", "جشنواره",
    "مراسم", "نذر خون", "گرامیداشت", "هفته دفاع مقدس",
    "مسابقه ورزشی", "قهرمانی", "تیم فوتبال", "المپیاد",
    "بارش باران", "هواشناسی", "پیش‌بینی آب‌وهوا",
]

# نشانه‌های قوی اثر ملی روی معیشت/درمان میلیون‌ها نفر
STRONG_INCLUDE = [
    "دستمزد", "حقوق کارگر", "حقوق بازنشسته", "مستمری",
    "همسان‌سازی", "سبد معیشت", "قدرت خرید", "خط فقر",
    "بیمه درمانی", "دفترچه درمان", "هزینه درمان", "دارو",
    "بیمه بیکاری", "قانون کار", "شورای عالی کار",
    "تامین اجتماعی", "بازنشستگی", "حداقل دستمزد",
    "افزایش حقوق", "پرداخت مستمری", "شستا", "دستمزد کارگر",
]


def keyword_prefilter(title: str, body: str) -> str:
    """خروجی: "reject" یا "unsure". تصمیم قطعیِ مثبت اینجا گرفته نمی‌شود؛
    چون کلیدواژه به‌تنهایی نمی‌تواند خبر محلیِ به‌ظاهر مرتبط را تشخیص دهد
    (مثلاً خبر آب کشاورزی که کلمه «معیشت» هم دارد ولی محلی و بی‌ربط است)."""
    text = f"{title} {body}"
    has_exclude = any(k in text for k in HARD_EXCLUDE)
    has_include = any(k in text for k in STRONG_INCLUDE)
    if has_exclude and not has_include:
        return "reject"
    return "unsure"


# ---------------------------------------------------------------------------
# مرحله ۲: داوری نهایی با مدل زبانی رایگان (Google Gemini Flash)
# ---------------------------------------------------------------------------
# کلید رایگان: aistudio.google.com → Get API key
# در GitHub Actions به‌صورت Secret با نام GEMINI_API_KEY ذخیره کنید.

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
)

JUDGE_PROMPT_TEMPLATE = """تو ویراستار یک رسانه تخصصی کارگری/بازنشستگی هستی.
فقط خبرهایی را relevant=true بزن که اثر مستقیم و محسوس روی معیشت
(حقوق، دستمزد، قدرت خرید، مستمری) یا درمان (بیمه، دارو، هزینه درمان)
میلیون‌ها کارگر و بازنشسته سراسر کشور دارند.

relevant=false بزن برای:
- اخبار محلی یک شهر/استان بدون اثر ملی (مثل مدیریت آب یک استان، حتی اگر کلمه معیشت/کشاورز داشته باشد)
- اخبار تشریفاتی، مراسم، ورزشی، نذورات — حتی اگر کلمه «کارگر» یا «بازنشسته» در آن باشد
- تحلیل کلی بدون خبر مشخص، آگهی، یا شایعه بدون منبع رسمی

فقط خروجی JSON بده، دقیقاً به این شکل و بدون هیچ توضیح اضافه:
[{{"id": 0, "relevant": true}}, {{"id": 1, "relevant": false}}]

اخبار برای بررسی:
{news_json}
"""


def judge_with_ai(candidates: list) -> dict:
    """candidates: [{"id": int, "title": str, "body": str}, ...]
    خروجی: دیکشنری {id: True/False}
    همه موارد را یکجا در یک تماس می‌فرستد تا کوتای رایگان کمینه مصرف شود."""
    if not GEMINI_API_KEY or not candidates:
        # بدون کلید یا بدون ورودی → محافظه‌کارانه رد کن تا خبر بی‌ربط ارسال نشود
        return {c["id"]: False for c in candidates}

    news_json = json.dumps(
        [{"id": c["id"], "title": c["title"], "body": c["body"][:250]} for c in candidates],
        ensure_ascii=False,
    )
    payload = {
        "contents": [{"parts": [{"text": JUDGE_PROMPT_TEMPLATE.format(news_json=news_json)}]}],
        "generationConfig": {"temperature": 0, "response_mime_type": "application/json"},
    }

    try:
        resp = requests.post(GEMINI_URL, json=payload, timeout=30)
        resp.raise_for_status()
        raw_text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        verdicts = json.loads(raw_text)
        return {v["id"]: bool(v.get("relevant")) for v in verdicts}
    except Exception as e:
        print(f"⚠️ خطای فیلتر هوشمند (fallback به رد محافظه‌کارانه): {e}")
        return {c["id"]: False for c in candidates}


# ---------------------------------------------------------------------------
# تابع اصلی — همین را در اسکریپت فعلی صدا بزنید
# ---------------------------------------------------------------------------

def filter_relevant_news(news_items: list) -> list:
    """news_items: هر آیتم باید کلید 'title' و 'body' یا 'summary' داشته باشد.
    خروجی: فقط اخبار واقعاً مرتبط با معیشت/درمان کارگران و بازنشستگان."""
    candidates = []
    for idx, item in enumerate(news_items):
        title = item.get("title", "")
        body = item.get("body", item.get("summary", ""))
        if keyword_prefilter(title, body) == "reject":
            continue
        candidates.append({"id": idx, "title": title, "body": body})

    if not candidates:
        return []

    verdicts = judge_with_ai(candidates)
    return [news_items[c["id"]] for c in candidates if verdicts.get(c["id"])]
