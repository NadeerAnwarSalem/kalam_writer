# -*- coding: utf-8 -*-
"""
Tafsir Summarizer (Ollama)
---------------------------
Fetches tafsir (explanation) texts for a given ayah from several of the
best-known mufassirin via the Quran.com API, then sends them to a local
Ollama model to produce ONE clean Arabic summary (fully vocalized with
tashkeel/harakat) that:

  - contains nothing but the summary itself (no intros like "here is a
    summary of...")
  - never quotes any ayah verbatim, only refers to it (e.g. "كما قال الله
    تعالى في سورة البقرة الآية 286...")

Requirements:
    pip install requests
    Ollama must be installed and running locally (https://ollama.com)
    Pull a strong Arabic-capable model first, e.g.:
        ollama pull command-r7b-arabic
    (or swap OLLAMA_MODEL below for whichever model you prefer, e.g.
     "aya-expanse", "qwen2.5:14b", etc.)

Usage:
    python tafsir_summarizer.py

LAST FILE WE RAN IS 5:101
"""

import os
import re
import html
from ollama import chat
import requests
from refs import refs, more_refs
from supabase_client import upsert_aya
from tafsir_translate_supabase import fetch_english_ayah
from builder import QuranpediaClient

# ---------------------------------------------------------------------------
# Configuration — tweak as needed
# ---------------------------------------------------------------------------
API_BASE = "https://api.quran.com/api/v4"
SAVE_ROOT = r"G:\My Drive\Descriptions"

OLLAMA_MODEL = "gemma4:31b-cloud"   

# How many of the "best" tafsirs to combine before summarizing
NUM_TAFSIRS = 3

# Language of the tafsir resources fetched from Quran.com.
# English and Arabic tafsirs are separate resources on the API (not
# auto-translated), so this controls which ones you actually get back.
TAFSIR_LANGUAGE = "ar"

# Preferred author ordering. Each entry is a list of possible substrings
# (Arabic and/or English) that might appear in that author's resource name,
# since the API returns names in whatever TAFSIR_LANGUAGE you requested.
PREFERRED_ORDER = [
    ["ابن كثير", "ibn kathir"],
    ["الطبري", "tabari"],
    ["القرطبي", "qurtubi"],
    ["الميسر", "muyassar"],
    ["السعدي", "saadi"],
]

quranpedia_client = QuranpediaClient()

SYSTEM_PROMPT_AR = """أنتَ مُساعدٌ ذكيٌّ متخصِّصٌ في تلخيصِ كُتُبِ التَّفسيرِ القرآنيِّ باللُّغةِ العربيَّةِ. مُهمَّتُكَ أن تقرأَ النُّصوصَ التَّفسيريَّةَ المُقدَّمةَ من عِدَّةِ مُفسِّرينَ حولَ آيةٍ مُعيَّنةٍ، ثُمَّ تكتبَ مُلخَّصًا واحدًا شاملًا ودقيقًا يجمعُ أهمَّ ما جاءَ في هذهِ التَّفاسيرِ.

قواعدُ صارِمةٌ يجبُ الالتزامُ بها دائمًا دونَ استثناءٍ:

١. اكتُبِ المُلخَّصَ باللُّغةِ العربيَّةِ الفُصحى معَ التَّشكيلِ الكامِلِ (الحركاتِ) على جميعِ الكلماتِ.
٢. قدِّمِ المُلخَّصَ مُباشَرةً دونَ أيِّ مُقدِّماتٍ أو عباراتٍ تمهيديَّةٍ مِثلَ "فيما يلي مُلخَّصٌ" أو "هذا مُلخَّصٌ لتفسيرِ الآيةِ"، ابدأْ مُباشَرةً بالمُحتوى، وأنهِ الكلامَ بانتهاءِ المُلخَّصِ دونَ خاتمةٍ أو تعليقٍ.
٣. مَمنوعٌ مَنعًا باتًّا نقلُ نصِّ أيِّ آيةٍ قرآنيَّةٍ حرفيًّا داخلَ المُلخَّصِ، حتَّى لو وردَتْ في نُصوصِ التَّفاسيرِ المصدريَّةِ. بدلًا من ذلك أشِرْ إلى الآيةِ بذِكرِ اسمِ السُّورةِ ورقمِ الآيةِ فقط، مِثالُ ذلك: "كما قالَ اللهُ تعالى في سُورةِ البقرةِ الآيةِ ٢٨٦..." دونَ كتابةِ نصِّ الآيةِ نفسِها.
٤. لا تكتُبْ أيَّ نصٍّ خارجَ المُلخَّصِ نفسِه، لا تعليقاتٍ ولا خاتمةً ولا توضيحاتٍ إضافيَّةً ولا عناوينَ.
٥. اجعلِ المُلخَّصَ مُتماسِكًا واضِحًا مُفيدًا، يعكسُ أهمَّ المعاني والفوائدِ والأحكامِ التي ذكرَها المُفسِّرونَ، بأسلوبِكَ الخاصِّ لا بنقلِ عباراتِهم حرفيًّا.
٦. مَمنوعٌ استخدامُ رمزِ "ﷺ" أو أيِّ رمزٍ مُختصَرٍ للصَّلاةِ والسَّلامِ على النَّبيِّ ﷺ، بل اكتُبِ الصِّيغةَ كاملةً بحروفِها في كلِّ مرَّةٍ، أي: "صلَّى اللهُ عليهِ وسلَّمَ".
٧. إذا كانَ للآيةِ سببُ نزولٍ، فاجعلْ قصَّةَ سببِ النُّزولِ واضِحةً بيِّنةً ضِمنَ المُلخَّصِ. وإذا لم يكُنْ لها سببُ نزولٍ، فاجعلِ المقصودَ والحُكمَ والتَّوجيهَ الَّذي تحملُه الآيةُ واضِحًا بيِّنًا. وفي الحالَتَينِ اكتُبِ المُلخَّصَ بأسلوبٍ مُحبَّبٍ صادقِ المشاعرِ يلامسُ القلبَ، بحيثُ ينجذبُ القارئُ أو المُستمعُ إليهِ ويتأثَّرُ بهِ ويستوعِبُه استيعابًا كامِلًا."""


def strip_html(raw_html: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw_html)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def get_tafsir_resources(language: str = TAFSIR_LANGUAGE):
    resp = requests.get(f"{API_BASE}/resources/tafsirs", params={"language": language})
    resp.raise_for_status()
    resources = resp.json().get("tafsirs", [])

    def sort_key(res):
        # Check both "name" and "author_name" since either field might carry
        # the recognizable part of the author's name depending on language.
        haystack = f"{res.get('name', '')} {res.get('author_name', '')}".lower()
        for i, variants in enumerate(PREFERRED_ORDER):
            if any(v.lower() in haystack for v in variants):
                return i
        return len(PREFERRED_ORDER)

    return sorted(resources, key=sort_key)


def get_tafsir_text(tafsir_id: int, verse_key: str):
    resp = requests.get(f"{API_BASE}/tafsirs/{tafsir_id}/by_ayah/{verse_key}")
    resp.raise_for_status()
    data = resp.json().get("tafsir", {})
    return data.get("text", ""), data.get("resource_name", "Unknown")


def fetch_arabic_text(surah: int, ayah: int) -> str:
    """Arabic ayah text (with tashkeel) from the Quranpedia API."""
    ayah_data = quranpedia_client.get_ayah(surah, ayah)
    if not ayah_data or not ayah_data.get("text"):
        raise ValueError(f"لم يُعثَرْ على نصِّ الآيةِ {surah}:{ayah} في Quranpedia.")
    return ayah_data["text"]


def collect_tafsirs(verse_key: str, limit: int):
    """Grab tafsir text from the top `limit` preferred authors that actually
    have content for this ayah."""
    resources = get_tafsir_resources()
    collected = []
    for res in resources:
        if len(collected) >= limit:
            break
        try:
            raw_text, author = get_tafsir_text(res["id"], verse_key)
        except requests.RequestException as e:
            print(f"(تعذّر جلب تفسير {res.get('name')}: {e})")
            continue
        if raw_text:
            collected.append((author, strip_html(raw_text)))
    return collected


def call_ollama(user_prompt: str) -> str:
    resp = chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT_AR},
            {"role": "user", "content": user_prompt},
        ]
    )
    return resp.message.content


def build_user_prompt(surah: str, ayah: str, tafsirs: list) -> str:
    parts = [f"إليكَ نُصوصُ التَّفسيرِ التاليةُ للآيةِ رقمِ {ayah} مِن سُورةِ رقمِ {surah} (بالتَّرقيمِ)، لخِّصْها وفقَ القواعدِ المذكورةِ:\n"]
    for author, text in tafsirs:
        parts.append(f"\n--- تفسير: {author} ---\n{text}\n")
    return "\n".join(parts)


def save_summary(surah: int, ayah: int, arabic_text: str, summary: str,
                  has_asbab_nuzul: bool, english_ayah: str):

    data = {
        "surah": surah,
        "ayah_number": ayah,
        "arabic_text": arabic_text,
        "full_description": summary,
        "has_asbab_nuzul": has_asbab_nuzul,
        "english_ayah": english_ayah,
    }
    upsert_aya(data)
    print(f"DONE! SAVED TO DATABASE.")


def process_ayah(surah: int, ayah: int, has_asbab_nuzul: bool):

    verse_key = f"{surah}:{ayah}"

    print("جارٍ جلبُ نصِّ الآيةِ...")
    try:
        arabic_text = fetch_arabic_text(surah, ayah)
    except (requests.RequestException, ValueError) as e:
        print(f"فشلَ الاتِّصالُ بواجهةِ Quranpedia: {e}\n")
        return True

    print("جارٍ جلبُ التَّفاسيرِ...")
    try:
        tafsirs = collect_tafsirs(verse_key, NUM_TAFSIRS)
    except requests.RequestException as e:
        print(f"فشلَ الاتِّصالُ بواجهةِ Quran.com: {e}\n")
        return True

    if not tafsirs:
        print("لم يُعثَرْ على أيِّ تفسيرٍ لهذهِ الآيةِ.\n")
        return True

    try:
        english_ayah = fetch_english_ayah(surah, ayah)
    except (requests.RequestException, ValueError) as e:
        print(f"فشلَ جلبُ الآيةِ بالإنجليزيَّةِ: {e}\n")
        return True

    prompt = build_user_prompt(surah, ayah, tafsirs)

    while True:
        print("جارٍ تلخيصُ التَّفاسيرِ عبرَ Ollama...")
        try:
            summary = call_ollama(prompt)
        except requests.RequestException as e:
            print(f"تعذَّرَ الاتِّصالُ بـ Ollama (تأكَّدْ أنَّه يعملُ محلِّيًّا): {e}\n")
            return True

        print(f"\n{'=' * 60}\n{summary}\n{'=' * 60}\n")
        save_summary(surah, ayah, arabic_text, summary, has_asbab_nuzul, english_ayah)
        print()
        return True

def main():
    for surah, ayah, has_asbab_nuzul in more_refs:
        print(f"processing {surah}:{ayah}")
        process_ayah(surah, ayah, has_asbab_nuzul)


if __name__ == "__main__":
    main()