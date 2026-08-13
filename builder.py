import time
import requests

from functools import lru_cache


BASE = "https://api.quranpedia.net/v1"
HAFS_MUSHAF_ID = 1

SHORT_TAFSIR_HINTS = ["الميسر", "مختصر", "التسهيل"]
ASBAB_SEARCH_QUERIES = ["أسباب النزول", "اسباب النزول"]



class QuranpediaClient:
    def __init__(self, session: requests.Session = None, pause: float = 0.15):
        self.session = session or requests.Session()
        self.pause = pause

    def _get(self, path:str):
        url = f"{BASE}{path}"
        r = self.session.get(url, timeout=20)
        time.sleep(self.pause)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()

    def get_ayah(self, surah:int, ayah:int):
        """Arabic text + metadata for one ayah."""
        return self._get(f"/mushafs/{HAFS_MUSHAF_ID}/{surah}/{ayah}")

    def get_surah_ayahs(self, surah:int):
        """All ayahs (with Arabic text) for a surah."""
        return self._get(f"/mushafs/{HAFS_MUSHAF_ID}/{surah}") or []

    def get_surah_tafsir_books(self, surah:int):
        """List of tafsir books for a surah."""
        return self._get(f"/surah/tafsirs/{surah}") or []

    def get_book_content_for_ayah(self, surah: int, ayah: int, book_id: int):
        "Tafsir/asbab text for a specific ayah from a specific book."
        return self._get(f"/ayah/{surah}/{ayah}/book/{book_id}")

    @lru_cache(maxsize=1)
    def get_asbab_book_ids(self):
        "Search once, cache the result: book ids that are asbab al-nuzul works."
        ids = set()
        for q in ASBAB_SEARCH_QUERIES:
            data = self._get(f"/search/{q}/books")
            if not data:
                continue
            for item in data.get("items", []):
                info = item.get("book_info", {})
                if info.get("id"):
                    ids.add(info["id"])
        return tuple(ids)

class QuranArticleBuilder:
    """Builds the {arabic_text, short_description, full_description} for a given ayah."""

    def __init__(self, client: QuranpediaClient = None):
        self.client = client or QuranpediaClient()

    def _pick_short_tafsir_book(self, surah:int):
        """Pick a tafsir book that is likely to be short and concise."""
        books = self.client.get_surah_tafsir_books(surah)
        if not books:
            return None

        for hint in SHORT_TAFSIR_HINTS:
            for book in books:
                if hint in book.get("name", ""):
                    return book

        return books[0]  # fallback to the first book if no short one is found

    def _pick_fuller_tafsir_book(self, surah:int, exclude_id=None):
        books = self.client.get_surah_tafsir_books(surah)
        for b in books:
            if b.get("id") != exclude_id:
                return b
        return books[0] if books else None

    def build(self, surah:int, ayah:int):
        ayah_data = self.client.get_ayah(surah, ayah)
        if not ayah_data:
            raise ValueError(f"No ayah data found for surah {surah}, ayah {ayah}")

        arabic_text = ayah_data["text"]

        #---------SHORT DESCRIPTION---------
        short_book = self._pick_short_tafsir_book(surah)
        short_description, short_source = "", None
        if short_book:
            content = self.client.get_book_content_for_ayah(surah, ayah, short_book["id"])
            if content and content.get("content"):
                short_description = " ". join(c["text"] for c in content["content"]).strip()
                short_source = short_book["name"]

        #---------FULL DESCRIPTION---------
        full_description, full_source, has_asbab = "", None, False
        for book_id in self.client.get_asbab_book_ids():
            content = self.client.get_book_content_for_ayah(surah, ayah, book_id)
            if content and content.get("content"):
                full_description = " ".join(c["text"] for c in content["content"]).strip()
                full_source = content["book"]["name"]
                has_asbab = True
                break

        if not full_description:
            fuller_book = self._pick_fuller_tafsir_book(surah, exclude_id=short_book["id"] if short_book else None)
            if fuller_book:
                content = self.client.get_book_content_for_ayah(surah, ayah, fuller_book["id"])
                if content and content.get("content"):
                    full_description = " ".join(c["text"] for c in content["content"]).strip()
                    full_source = fuller_book["name"]

        return {
            "surah": surah,
            "ayah_number": ayah,
            "arabic_text": arabic_text,
            "short_description": short_description,
            "full_description": full_description,
            "has_asbab_nuzul": has_asbab,
            "short_source": short_source,
            "full_source": full_source,
        }

    def build_surah(self, surah: int, start: int = 1, end: int = None):
        """Build articles for a whole range of a surah (or all of it)."""
        if end is None:
            all_ayahs = self.client._get(f"/mushafs/{HAFS_MUSHAF_ID}/{surah}")
            end = len(all_ayahs)
        return [self.build(surah, a) for a in range(start, end + 1)]
