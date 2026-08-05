# -*- coding: utf-8 -*-
"""
Article Translator (Supabase)
------------------------------
Translates article fields between Arabic and English via the local Ollama
model, following strict rules (no verbatim ayah quoting, no preamble,
faithful tone -- hadith wording MAY be translated literally/directly).

Two ways to use this module:

1. Import `translate_article()` to translate a single article on submission
   (e.g. from main.py, right after a user creates a new article), so the
   missing-language fields get filled in immediately.

2. Run this file directly to backfill any existing rows in the `articles`
   table whose target-language fields are still empty, based on each row's
   `language` column ("Arabic" or "English").

Requirements:
    pip install supabase ollama

Usage:
    python articles_translate_supabase.py
"""

import time
from supabase import create_client, Client
from ollama import chat
import os
# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TABLE_NAME = "articles"
ID_COLUMN = "id"

# The three translatable fields on an article, and how they map to the
# language-prefixed columns in Supabase (e.g. "title" -> "arabic_title").
FIELD_KEYS = ["title", "summary", "content"]

LANGUAGES = ("Arabic", "English")

OLLAMA_MODEL = "gemma4:31b-cloud"

DELAY_SECONDS = 1

# Skip rows whose target-language content is already populated, so
# re-running the script is safe/cheap. Set to False to force re-translation.
SKIP_ALREADY_TRANSLATED = True

SYSTEM_PROMPT_AR_TO_EN = """You are a skilled Islamic-studies translator. You will be given a piece of \
Arabic text from an article about the context/story behind a Quranic ayah, and your task is to \
translate it into clear, natural English.

Strict rules you must always follow, without exception:

1. Output nothing but the translation itself — no preamble, no phrases like "Here is the \
translation" or "Translation:", no closing remarks, no headings.
2. Never quote the wording of any Quranic ayah verbatim in English, even if the Arabic source \
text quotes it. Instead, refer to it by mentioning the surah name and ayah number only, e.g. \
"as Allah says in Surah Al-Baqarah, verse 286..." — never reproduce the ayah's actual wording.
3. Hadith wording is different: if the Arabic source text quotes a hadith, you MAY translate it \
literally/directly as part of the translation — this restriction only applies to Quranic ayat, \
not hadith.
4. Never use the "ﷺ" symbol or any abbreviated honorific. Always spell it out in full: \
"peace and blessings of Allah be upon him".
5. Preserve the meaning, structure, and tone of the original Arabic text — this is a faithful \
translation, not a new summary and not a shortened version.
6. Use clear, natural English phrasing rather than a stiff word-for-word translation."""

SYSTEM_PROMPT_EN_TO_AR = """You are a skilled Islamic-studies translator. You will be given a piece of \
English text from an article about the context/story behind a Quranic ayah, and your task is to \
translate it into clear, natural Arabic.

Strict rules you must always follow, without exception:

1. Output nothing but the translation itself — no preamble, no phrases like "إليك الترجمة" or \
"الترجمة:", no closing remarks, no headings.
2. Never quote the wording of any Quranic ayah verbatim, even if the English source text \
paraphrases or references it. Instead, refer to it by mentioning the surah name and ayah number \
only, e.g. "كما يقول الله تعالى في سورة البقرة، الآية 286..." — never reproduce the ayah's actual \
wording.
3. Hadith wording is different: if the English source text quotes a hadith, you MAY translate it \
literally/directly as part of the translation — this restriction only applies to Quranic ayat, \
not hadith.
4. Never use the "ﷺ" symbol. Always spell the honorific out in full: "صلى الله عليه وسلم".
5. Preserve the meaning, structure, and tone of the original English text — this is a faithful \
translation, not a new summary and not a shortened version.
6. Use clear, natural Arabic phrasing rather than a stiff word-for-word translation."""

# Which system prompt to use, keyed by the SOURCE language of the text being translated.
SYSTEM_PROMPTS = {
    "Arabic": SYSTEM_PROMPT_AR_TO_EN,
    "English": SYSTEM_PROMPT_EN_TO_AR,
}


def other_language(language: str) -> str:
    """Given "Arabic" or "English", return the opposite one."""
    return "English" if language == "Arabic" else "Arabic"


def db_column(language: str, field_key: str) -> str:
    """e.g. db_column("Arabic", "title") -> "arabic_title" """
    return f"{language.lower()}_{field_key}"


def translate_text(text: str, source_language: str) -> str:
    """Translate a single piece of text out of `source_language` into the other language."""
    resp = chat(
        model=OLLAMA_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPTS[source_language]},
            {"role": "user", "content": text},
        ]
    )
    return resp.message.content.strip()


def translate_article(article_data: dict, source_language: str) -> dict:
    """
    Translate an article's fields out of `source_language` ("Arabic" or "English")
    into the other language.

    `article_data` should have some subset of "title", "summary", "content" keys
    holding the source-language text. Returns a dict with the same keys holding
    the translated text (keys with empty/missing source text are omitted).
    """
    translated = {}
    for field_key in FIELD_KEYS:
        text = article_data.get(field_key)
        if not text:
            continue
        translated[field_key] = translate_text(text, source_language)
    return translated


# ---------------------------------------------------------------------------
# Batch backfill mode (python articles_translate_supabase.py)
# ---------------------------------------------------------------------------

def fetch_rows(client) -> list:
    columns = [ID_COLUMN, "language"] + [
        db_column(lang, key) for lang in LANGUAGES for key in FIELD_KEYS
    ]
    resp = client.table(TABLE_NAME).select(",".join(columns)).execute()
    return resp.data


def update_row(client, row_id, fields: dict):
    client.table(TABLE_NAME).update(fields).eq(ID_COLUMN, row_id).execute()


def process_one(client, row: dict) -> bool:
    row_id = row[ID_COLUMN]
    print(f"\n--- id {row_id} ---")

    source_language = row.get("language")
    if source_language not in LANGUAGES:
        print(f"  Unknown language {source_language!r}, skipping.")
        return False
    target_language = other_language(source_language)

    if SKIP_ALREADY_TRANSLATED and row.get(db_column(target_language, "content")):
        print("  Already translated, skipping.")
        return True

    article_data = {key: row.get(db_column(source_language, key)) for key in FIELD_KEYS}
    if not article_data.get("content"):
        print("  Source content is empty, skipping.")
        return False

    try:
        translated = translate_article(article_data, source_language)
    except Exception as e:
        print(f"  Failed to translate via Ollama: {e}")
        return False

    if not translated:
        print("  Nothing to translate.")
        return False

    update_fields = {db_column(target_language, key): value for key, value in translated.items()}

    try:
        update_row(client, row_id, update_fields)
    except Exception as e:
        print(f"  Failed to update Supabase row: {e}")
        return False

    print(f"  Done ({source_language} -> {target_language}).")
    return True


def main():
    url: str = os.environ.get("SUPABASE_URL")
    key: str = os.environ.get("SUPABASE_KEY")
    client: Client = create_client(url, key)
    rows = fetch_rows(client)

    success, failed = 0, []
    for row in rows:
        ok = process_one(client, row)
        if ok:
            success += 1
        else:
            failed.append(str(row.get(ID_COLUMN)))
        time.sleep(DELAY_SECONDS)

    print(f"\n{'=' * 40}")
    print(f"Succeeded: {success} / {len(rows)}")
    if failed:
        print(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
