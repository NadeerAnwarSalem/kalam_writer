import pandas as pd
import random
import string
import tempfile

from datetime import datetime, timezone
from utils import *
from supabase import create_client, Client
from tag_options import TAG_OPTIONS
from streamlit_cookies_manager import EncryptedCookieManager
from articles_translate_supabase import translate_article, other_language
from elevenlabs import ElevenLabs
from r2_client import upload_audio
from builder import QuranpediaClient, QuranArticleBuilder


url: str = st.secrets.get("SUPABASE_URL")
key: str = st.secrets.get("SUPABASE_KEY")
service_key: str = st.secrets.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(url, key)
supabase_admin: Client = create_client(url, service_key)

EL_API_KEY = st.secrets.get("ELEVENLABS_KEY")
MODEL_ID = "eleven_multilingual_v2"
elevenlabs_client = ElevenLabs(api_key=EL_API_KEY)
elevenlabs_user = elevenlabs_client.user.get()

ar_voices = [
    "XdoLPWNt7ytn6BtU4FBf",  # abdullah
    "fkqevZRU7Xj52dY1CTkq",  # hijazi
    "FOyke8LaC5kHLkRFE8oG",  # fahad
    "vszNunPGBVqJg1Qd4H7Z",
    "xvhpbk8otnNHtT3fjCpr",
]

en_voices = [
    "TTmUgRoiAUdn043OgRax",  # eddie
    "qSeXEcewz7tA0Q0qk9fH",  # viktoria
    "oaGwHLz3csUaSnc2NBD4",  # benedict
    "GrVxA7Ub86nJH91Viyiv",
]

OUTPUT_ROOT = Path(st.secrets.get("AUDIO_OUTPUT_DIR", tempfile.gettempdir())) / "kalam_audio"
OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

quranpedia_client = QuranpediaClient()

TADABBUR_MAX_VERSES = 10

image_bytes = None  # Initialize image_bytes to None
img_path = Path(__file__).parent / "bg_img.jpg"

cookies = EncryptedCookieManager(
    prefix="myapp/",
    password=st.secrets["COOKIES_PASSWORD"],  # any long random string in secrets.toml
)
if not cookies.ready():
    st.stop()  # wait for cookies to load before rendering anything else


def generate_audio(text: str, voice: str, out_path: Path):
    """Generate TTS audio for `text` with ElevenLabs and write it to out_path."""
    audio_stream = elevenlabs_client.text_to_speech.convert(
        voice_id=voice,
        text=text,
        model_id=MODEL_ID,
        output_format="mp3_44100_128",
    )
    with open(out_path, "wb") as f:
        for chunk in audio_stream:
            f.write(chunk)

if not st.session_state.get("logged_in") and cookies.get("refresh_token"):
    try:
        auth_response = supabase.auth.refresh_session(cookies.get("refresh_token"))
        if auth_response.user:
            st.session_state["logged_in"] = True
            st.session_state["user_email"] = auth_response.user.email
            st.session_state["user_id"] = auth_response.user.id
            st.session_state["username"] = auth_response.user.user_metadata.get("username", "")
            st.session_state["user_role"] = auth_response.user.app_metadata.get("role", "user")
            # refresh token may rotate — save the new one
            cookies["refresh_token"] = auth_response.session.refresh_token
            cookies.save()
    except Exception:
        # refresh token invalid/expired — treat as logged out
        cookies["refresh_token"] = ""
        cookies.save()


def update_article_status(article_id: str, new_status: str) -> bool:
    """Update a specific article's status in Supabase. Returns True on success."""
    try:
        supabase.table("articles").update({"status": new_status}).eq(
            "id", article_id
        ).execute()
        return True
    except Exception as exc:
        st.error(f"Failed to update status: {exc}")
        return False


def translate_and_update_article(article_id: str) -> bool:
    """Translate an approved article and update the translated fields in Supabase.

    Returns True on success, False on any failure (fetch, translation, or write).
    Does NOT re-translate if the target-language fields are already populated,
    so re-approving an article won't silently clobber a manually corrected
    translation.
    """
    try:
        article_response = (
            supabase.table("articles").select("*").eq("id", article_id).single().execute()
        )
    except Exception as exc:
        st.warning(f"Could not load article for translation: {exc}")
        return False

    if not article_response.data:
        st.warning("Article not found for translation.")
        return False

    article = article_response.data
    source_language = article["language"]
    target_language = other_language(source_language)

    # Skip re-translation if the target language content already exists.
    existing_target_title = article.get(f"{target_language.lower()}_title")
    if existing_target_title:
        return True

    payload = {
        "title": article[f"{source_language.lower()}_title"],
        "summary": article[f"{source_language.lower()}_summary"],
        "content": article[f"{source_language.lower()}_content"],
    }

    try:
        translated_article = translate_article(payload, source_language)
    except Exception as exc:
        st.warning(f"Translation failed: {exc}")
        return False

    update_data = {
        f"{target_language.lower()}_title": translated_article["title"],
        f"{target_language.lower()}_summary": translated_article["summary"],
        f"{target_language.lower()}_content": translated_article["content"],
    }

    try:
        supabase.table("articles").update(update_data).eq("id", article_id).execute()
    except Exception as exc:
        st.warning(f"Translation succeeded but saving it failed: {exc}")
        return False

    return True


def generate_and_upload_article_audio(article_id: str) -> bool:
    """Generate Arabic + English narration audio for an article and store the URLs.

    Returns True on success, False on any failure. Skips generation (and
    counts as success) if both audio URLs are already set, so re-approving
    an article won't burn ElevenLabs credits or overwrite existing audio.
    """
    try:
        article_response = (
            supabase.table("articles").select("*").eq("id", article_id).single().execute()
        )
    except Exception as exc:
        st.warning(f"Could not load article for audio generation: {exc}")
        return False

    if not article_response.data:
        st.warning("Article not found for audio generation.")
        return False

    article = article_response.data

    if article.get("ar_audio_url") and article.get("en_audio_url"):
        return True

    ar_title = article.get("arabic_title")
    ar_summary = article.get("arabic_summary")
    ar_content = article.get("arabic_content")
    en_title = article.get("english_title")
    en_summary = article.get("english_summary")
    en_content = article.get("english_content")

    if not all([ar_title, ar_summary, ar_content, en_title, en_summary, en_content]):
        st.warning("Cannot generate audio: article is missing bilingual title/summary/content.")
        return False

    if not article.get("slug") or not article.get("author_id"):
        st.warning("Cannot generate audio: article is missing a slug or author_id.")
        return False

    ar_voice = random.choice(ar_voices)
    en_voice = random.choice(en_voices)

    ar_out_path = OUTPUT_ROOT / "ar_articles" / ar_title / "title.mp3"
    en_out_path = OUTPUT_ROOT / "en_articles" / en_title / "title.mp3"

    try:
        ar_out_path.parent.mkdir(parents=True, exist_ok=True)
        en_out_path.parent.mkdir(parents=True, exist_ok=True)

        ar_txt = "\n\n".join([ar_summary, ar_content])
        en_txt = "\n\n".join([en_summary, en_content])

        generate_audio(ar_txt, ar_voice, ar_out_path)
        ar_r2_key = f"articles/{article['author_id']}/{article['slug']}/arabic_audio.mp3"
        ar_audio_url = upload_audio(ar_out_path, ar_r2_key)

        generate_audio(en_txt, en_voice, en_out_path)
        en_r2_key = f"articles/{article['author_id']}/{article['slug']}/english_audio.mp3"
        en_audio_url = upload_audio(en_out_path, en_r2_key)
    except Exception as exc:
        st.warning(f"Audio generation failed: {exc}")
        return False

    if not ar_audio_url or not en_audio_url:
        st.warning("Audio generation did not return a usable URL for one or both languages.")
        return False

    payload = {
        "ar_audio_url": ar_audio_url,
        "en_audio_url": en_audio_url,
    }
    try:
        supabase.table("articles").update(payload).eq("id", article_id).execute()
    except Exception as exc:
        st.warning(f"Audio was generated but saving the URLs failed: {exc}")
        return False

    return True


def process_status_changes(edited_rows: dict, df_articles: pd.DataFrame, editor_key: str):
    any_processed = False
    for index, change in edited_rows.items():
        if "Status" not in change:
            continue

        row = df_articles.iloc[index]
        row_article_id = row["ID"]
        new_status = change["Status"]

        author_wants_audio = row["With Audio"]
        approve_choice = change.get("Approve As", row["Approve As"])
        approve_with_audio_now = approve_choice == "With Audio"

        with st.spinner("Updating status..."):
            status_ok = update_article_status(row_article_id, new_status)

        if not status_ok:
            continue

        if new_status == "approved":
            with st.spinner("Translating and updating article..."):
                translation_ok = translate_and_update_article(row_article_id)

            if not translation_ok:
                st.warning("Status updated, but translation could not be completed.")
            elif not author_wants_audio:
                # Author never requested audio — nothing to generate, ever.
                st.success("Status updated and article translated. No audio requested by author.")
            elif approve_with_audio_now:
                with st.spinner("Generating audio for article..."):
                    audio_ok = generate_and_upload_article_audio(row_article_id)

                if audio_ok:
                    supabase.table("articles").update({"audio_generated": True}).eq("id", row_article_id).execute()
                    st.success("Status updated, article translated, and audio generated successfully!")
                else:
                    st.warning("Status updated and article translated, but audio generation could not be completed.")
            else:
                st.success("Status updated and article translated. Audio deferred — add it later below.")
        else:
            st.success("Status updated successfully!")

        any_processed = True

    if any_processed:
        st.session_state[editor_key]["edited_rows"] = {}
        st.rerun()


@st.cache_data(show_spinner=False)
def get_surah_ayah_count(surah: int) -> int:
    """Total ayah count for a surah, via Quranpedia. Falls back to 1 on API failure
    so number_input widgets that depend on this never end up with max < min."""
    try:
        ayahs = quranpedia_client.get_surah_ayahs(surah)
    except Exception:
        return 1
    return len(ayahs) if ayahs else 1


@st.cache_data(show_spinner=False)
def fetch_ayah_text(surah: int, ayah: int):
    """Arabic text for a single ayah, via Quranpedia. Returns None on failure."""
    try:
        data = quranpedia_client.get_ayah(surah, ayah)
    except Exception:
        return None
    return data.get("text") if data else None


def generate_tadabbur_slug(title: str, surah: int, start_ayah: int, end_ayah: int) -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
    if title and title.strip():
        base = title.strip().lower().replace(" ", "-")
    else:
        base = f"tadabbur-{surah}-{start_ayah}-{end_ayah}"
    return f"{base}-{suffix}"


def update_tadabbur_status(tadabbur_id: str, new_status: str) -> bool:
    """Update a specific tadabbur's status in Supabase. Returns True on success."""
    try:
        supabase.table("tadabbur").update({"status": new_status}).eq(
            "id", tadabbur_id
        ).execute()
        return True
    except Exception as exc:
        st.error(f"Failed to update status: {exc}")
        return False

def update_nugget_status(nugget_id: str, new_status: str) -> bool:
    """Update a specific nugget's status in Supabase. Returns True on success."""
    try:
        supabase.table("nuggets").update({"status": new_status}).eq(
            "id", nugget_id
        ).execute()
        return True
    except Exception as exc:
        st.error(f"Failed to update status: {exc}")
        return False


def translate_and_update_tadabbur(tadabbur_id: str) -> bool:
    """Translate an approved tadabbur into whichever language it's missing.

    Mirrors translate_and_update_article: source language comes from the
    `language` column. Only title/content are translated -- tadabbur has no
    summary field. Skips (and returns True) if the target-language content
    is already populated, so re-approving never clobbers a manually
    corrected translation.
    """
    try:
        response = supabase.table("tadabbur").select("*").eq("id", tadabbur_id).single().execute()
    except Exception as exc:
        st.warning(f"Could not load tadabbur for translation: {exc}")
        return False

    if not response.data:
        st.warning("Tadabbur not found for translation.")
        return False

    row = response.data
    source_language = row.get("language")
    if source_language not in ("Arabic", "English"):
        st.warning("Tadabbur has no recognized language set, skipping translation.")
        return False

    target_language = other_language(source_language)

    if row.get(f"{target_language.lower()}_content"):
        return True

    source_content = row.get(f"{source_language.lower()}_content")
    if not source_content:
        st.warning("Tadabbur has no content to translate.")
        return False

    payload = {"content": source_content}
    source_title = row.get(f"{source_language.lower()}_title")
    if source_title:
        payload["title"] = source_title

    try:
        translated = translate_article(payload, source_language)
    except Exception as exc:
        st.warning(f"Translation failed: {exc}")
        return False

    if not translated:
        return True

    update_data = {f"{target_language.lower()}_{key}": value for key, value in translated.items()}

    try:
        supabase.table("tadabbur").update(update_data).eq("id", tadabbur_id).execute()
    except Exception as exc:
        st.warning(f"Translation succeeded but saving it failed: {exc}")
        return False

    return True

def translate_and_update_nugget(nugget_id: str) -> bool:
    """Translate an approved nugget into whichever language it's missing.

    Mirrors translate_and_update_article: source language comes from the
    `language` column. Only title/content are translated -- nugget has no
    summary field. Skips (and returns True) if the target-language content
    is already populated, so re-approving never clobbers a manually
    corrected translation.
    """
    try:
        response = supabase.table("nugget").select("*").eq("id", nugget_id).single().execute()
    except Exception as exc:
        st.warning(f"Could not load nugget for translation: {exc}")
        return False

    if not response.data:
        st.warning("nugget not found for translation.")
        return False

    row = response.data
    source_language = row.get("language")
    if source_language not in ("Arabic", "English"):
        st.warning("nugget has no recognized language set, skipping translation.")
        return False

    target_language = other_language(source_language)

    if row.get(f"{target_language.lower()}_text"):
        return True

    source_text = row.get(f"{source_language.lower()}_text")
    if not source_text:
        st.warning("nugget has no content to translate.")
        return False

    payload = {"text": source_text}

    try:
        translated = translate_article(payload, source_language)
    except Exception as exc:
        st.warning(f"Translation failed: {exc}")
        return False

    if not translated:
        return True

    update_data = {f"{target_language.lower()}_{key}": value for key, value in translated.items()}

    try:
        supabase.table("nugget").update(update_data).eq("id", nugget_id).execute()
    except Exception as exc:
        st.warning(f"Translation succeeded but saving it failed: {exc}")
        return False

    return True


def backfill_missing_ayat_for_tadabbur(surah: int, start_ayah: int, end_ayah: int) -> None:
    """Ensure every ayah in [start_ayah, end_ayah] of `surah` exists in the ayat
    table, fetching+inserting any that are missing via the same
    QuranArticleBuilder pipeline the Quran-curation scripts already use (Arabic
    text + tafsir + asbab from Quranpedia). Uses supabase_admin since writing to
    ayat requires the service role key (ayat is read-only for the anon key).

    A failure fetching/inserting one ayah is logged and skipped -- it never
    blocks the rest of the range or the tadabbur approval itself. Ayahs already
    present in ayat are left untouched, never re-fetched or overwritten.
    """
    builder = QuranArticleBuilder(quranpedia_client)
    for ayah in range(start_ayah, end_ayah + 1):
        try:
            existing = (
                supabase_admin.table("ayat")
                .select("id")
                .eq("surah", surah)
                .eq("ayah_number", ayah)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            st.warning(f"Could not check ayat table for {surah}:{ayah}: {exc}")
            continue

        if existing.data:
            continue

        try:
            data = builder.build(surah, ayah)
            supabase_admin.table("ayat").upsert(data, on_conflict="surah,ayah_number").execute()
        except Exception as exc:
            st.warning(f"Could not fetch/insert ayah {surah}:{ayah} into ayat: {exc}")
            continue


def process_tadabbur_status_changes(edited_rows: dict, df_tadabbur: pd.DataFrame, editor_key: str):
    any_processed = False
    for index, change in edited_rows.items():
        if "Status" not in change:
            continue

        row = df_tadabbur.iloc[index]
        row_tadabbur_id = row["ID"]
        new_status = change["Status"]

        with st.spinner("Updating status..."):
            status_ok = update_tadabbur_status(row_tadabbur_id, new_status)

        if not status_ok:
            continue

        if new_status == "approved":
            with st.spinner("Translating tadabbur..."):
                translation_ok = translate_and_update_tadabbur(row_tadabbur_id)

            with st.spinner("Checking tafsir coverage for referenced verses..."):
                backfill_missing_ayat_for_tadabbur(
                    int(row["Surah"]), int(row["Start Ayah"]), int(row["End Ayah"])
                )

            if not translation_ok:
                st.warning("Status updated, but translation could not be completed.")
            else:
                st.success("Status updated, tadabbur translated, and verse coverage checked.")
        else:
            st.success("Status updated successfully!")

        any_processed = True

    if any_processed:
        st.session_state[editor_key]["edited_rows"] = {}
        st.rerun()

def process_nugget_status_changes(edited_rows: dict, df_nugget: pd.DataFrame, editor_key: str):
    any_processed = False
    for index, change in edited_rows.items():
        if "Status" not in change:
            continue

        row = df_nugget.iloc[index]
        row_nugget_id = row["ID"]
        new_status = change["Status"]

        with st.spinner("Updating status..."):
            status_ok = update_nugget_status(row_nugget_id, new_status)

        if not status_ok:
            continue

        if new_status == "approved":
            with st.spinner("Translating nugget..."):
                translation_ok = translate_and_update_nugget(row_nugget_id)

            if not translation_ok:
                st.warning("Status updated, but translation could not be completed.")
            else:
                st.success("Status updated, nugget translated.")
        else:
            st.success("Status updated successfully!")

        any_processed = True

    if any_processed:
        st.session_state[editor_key]["edited_rows"] = {}
        st.rerun()


@st.dialog("Edit Article Dialog", dismissible=True)
def edit_article_dialog(article_data: dict):
    lang = article_data["language"]
    st.title(f"Edit Article: {article_data[f'{lang.lower()}_title']}")
    new_title = st.text_input("Title", value=article_data[f"{lang.lower()}_title"], disabled=True)
    new_summary = st.text_area("Summary", value=article_data[f"{lang.lower()}_summary"])
    write, pdf, word, txt = st.tabs(["Write", "PDF", "Word", "Text"])
    with write:
        new_content = st.text_area("Content", value=article_data[f"{lang.lower()}_content"], max_chars=10000, height=300)
    with pdf:
        uploaded_pdf = st.file_uploader("Upload PDF", type=["pdf"])
        if uploaded_pdf:
            new_content = extract_text(uploaded_pdf, "pdf")
    with word:
        uploaded_word = st.file_uploader("Upload Word Document", type=["docx", "doc"])
        if uploaded_word:
            new_content = extract_text(uploaded_word, "docx")
    with txt:
        uploaded_txt = st.file_uploader("Upload Text File", type=["txt"])
        if uploaded_txt:
            new_content = extract_text(uploaded_txt, "txt")

    new_article_tags = st.multiselect("Hashtags", options=TAG_OPTIONS, max_selections=5, default=article_data["tags"])
    new_author = st.text_input("Author", value=article_data['article_author'], disabled=True)
    new_with_audio = st.checkbox(
        "Include AI-generated audio for this article?",
        help="If selected, AI-generated audio will be added to your article within 30 days.",
        value=article_data.get("with_audio", False),
        )

    # new_image = st.file_uploader("Upload New Image", type=["png", "jpg", "jpeg"])

    if st.button("Save Changes"):
        updated_data = {
            f"{lang.lower()}_title": new_title,
            f"{lang.lower()}_summary": new_summary,
            f"{lang.lower()}_content": new_content,
            "article_author": new_author,
            "tags": new_article_tags, 
            "with_audio": new_with_audio,
        }
        try:
            supabase.table("articles").update(updated_data).eq("id", article_data['id']).execute()
            st.success("Article updated successfully!")
            st.rerun()
        except Exception as exc:
            st.error(f"Failed to update article: {exc}")


article_id = st.query_params.get("article_id")

if not article_id:
    if not st.session_state.get("logged_in"):
        with st.form("login_form"):
            st.write("Login Form")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")

            if submitted:
                try:
                    response = supabase.auth.sign_in_with_password(
                        {
                            "email": email,
                            "password": password,
                        }
                    )
                except Exception as exc:
                    response = None
                    st.error(f"Login failed: {exc}")

                if response and response.user:
                    st.success("Login successful!")
                    st.session_state["logged_in"] = True
                    st.session_state["user_email"] = response.user.email
                    st.session_state["user_id"] = response.user.id
                    st.session_state["username"] = response.user.user_metadata.get("username", "")
                    st.session_state["user_role"] = response.user.app_metadata.get("role", "user")
                    # persist across refreshes
                    cookies["refresh_token"] = response.session.refresh_token
                    cookies.save()
                    st.rerun()
                elif response is not None:
                    st.error("Login failed!")
    else:
        if st.button("refresh"):
            st.rerun()

        # ---- admin dashboard ----
        if st.session_state.get("user_role") == "admin":
            st.write(f"Welcome, {st.session_state['username']}! (Admin)")

            create_user_tab, manage_data_tab = st.tabs(["Create User", "Manage Data"])
            with create_user_tab:
                with st.expander("Create User"):
                    with st.form("create_user_form"):
                        st.write("Create User Form")
                        new_email = st.text_input("New User Email")
                        new_password = st.text_input("New User Password", type="password")
                        new_username = st.text_input("New User Username")
                        new_role = st.selectbox("New User Role", ["user", "admin"])
                        create_submitted = st.form_submit_button("Create User")

                        if create_submitted:
                            try:
                                response = supabase_admin.auth.admin.create_user(
                                    {
                                        "email": new_email,
                                        "password": new_password,
                                        "email_confirm": True,
                                        "user_metadata": {
                                            "username": new_username
                                        },
                                        "app_metadata": {
                                            "role": new_role
                                        }
                                    }
                                )
                            except Exception as exc:
                                response = None
                                st.error(f"Failed to create user: {exc}")

                            if response and response.user:
                                st.success(f"User {new_email} created successfully!")
                            elif response is not None:
                                st.error("Failed to create user!")

            with manage_data_tab:
                with st.container(border=True, height=500):
                    users_tab, articles_tab, tadabbur_tab, nuggets_tab= st.tabs(["Users", "Articles", "Tadabbur", "Nuggets"])
                    with users_tab:
                        st.write("List of Users")
                        users = supabase_admin.auth.admin.list_users()
                        users = [(user.id, user.email, user.user_metadata.get("username", "")) for user in users]
                        df = pd.DataFrame(users, columns=["User ID", "Email", "Username"])
                        if users:
                            st.table(df)
                        else:
                            st.write("No users found.")

                    with articles_tab:
                        st.write("List of Articles")
                        articles_data = supabase.table("articles").select("*").execute().data

                        articles = [
                            (
                                a["id"],
                                a["article_author"],
                                a[f"{a['language'].lower()}_title"],
                                a["status"],
                                bool(a.get("with_audio") or False),      # author's preference, read-only
                                bool(a.get("audio_generated") or False), # actual generation state, hidden field
                                "With Audio",  # default choice when approving
                                "Actions",
                                "Length",
                            )
                            for a in articles_data
                        ]
                        df_articles = pd.DataFrame(
                            articles,
                            columns=["ID", "Article Author", "Title", "Status", "With Audio", "Audio Generated", "Approve As", "Actions", "Length"],
                        )
                        df_articles["Actions"] = df_articles["ID"].apply(
                            lambda aid: f"/Article_Reader?article_id={aid}"
                        )
                        df_articles["Length"] = [
                            len(
                                (a.get("arabic_summary") or "")
                                + (a.get("arabic_content") or "")
                                + (a.get("english_summary") or "")
                                + (a.get("english_content") or "")
                            )
                            for a in articles_data
                        ]

                        admin_editor_key = "admin_article_table_editor"
                        st.data_editor(
                            df_articles,
                            use_container_width=True,
                            column_order=["Article Author", "Title", "Status", "With Audio", "Approve As", "Actions", "Length"],
                            column_config={
                                "Article Author": st.column_config.TextColumn("Article Author", disabled=True),
                                "Title": st.column_config.TextColumn("Title", disabled=True),
                                "Status": st.column_config.SelectboxColumn(
                                    "Status",
                                    help="Toggle to change article status",
                                    options=["pending", "approved", "rejected"],
                                ),
                                "With Audio": st.column_config.CheckboxColumn(
                                    "With Audio",
                                    help="Author's preference — set at article creation, cannot be changed here",
                                    disabled=True,
                                ),
                                "Approve As": st.column_config.SelectboxColumn(
                                    "Approve As",
                                    help="Only applies if the author requested audio: generate now, or add it later",
                                    options=["With Audio", "No Audio (add later)"],
                                ),
                                "Actions": st.column_config.LinkColumn(
                                    "Read Article",
                                    display_text="Read Article",
                                ),
                                "Length": st.column_config.NumberColumn(
                                    "Length",
                                    disabled=True,
                                    help="Approx. this is half of credits this article will use when narrated",
                                ),
                            },
                            disabled=["Article Author", "Title", "With Audio", "Length"],
                            hide_index=True,
                            key=admin_editor_key,
                        )

                        edited_rows = st.session_state.get(admin_editor_key, {}).get("edited_rows", {})
                        if edited_rows:
                            process_status_changes(edited_rows, df_articles, admin_editor_key)

                        # --- Add audio later: author wanted audio, approved, but not generated yet ---
                        pending_audio_df = df_articles[
                            (df_articles["Status"] == "approved")
                            & (df_articles["With Audio"])           # author actually requested it
                            & (~df_articles["Audio Generated"])     # but it hasn't been made yet
                        ]

                        if not pending_audio_df.empty:
                            st.divider()
                            st.write("**Add audio to approved articles**")

                            audio_choice_id = st.selectbox(
                                "Select an article to generate audio for",
                                options=pending_audio_df["ID"],
                                format_func=lambda aid: pending_audio_df.loc[
                                    pending_audio_df["ID"] == aid, "Title"
                                ].values[0],
                                key="pending_audio_select",
                            )

                            col_btn, col_txt = st.columns([1,2])
                            with col_btn:
                                if st.button("Generate Audio Now", key="generate_audio_btn"):
                                    with st.spinner("Generating audio for article..."):
                                        audio_ok = generate_and_upload_article_audio(audio_choice_id)

                                    if audio_ok:
                                        supabase.table("articles").update({"audio_generated": True}).eq("id", audio_choice_id).execute()
                                        st.success("Audio generated and uploaded successfully!")
                                        st.rerun()
                                    else:
                                        st.error("Audio generation could not be completed.")

                            selected_length = pending_audio_df.loc[
                                pending_audio_df["ID"] == audio_choice_id, "Length"
                            ].iloc[0]

                            with col_txt:
                                st.write(f"This article will use approximately **{selected_length}** credits when narrated.")

                    with tadabbur_tab:
                        st.write("List of Tadabbur")
                        tadabbur_data = supabase.table("tadabbur").select("*").execute().data

                        def _tadabbur_title_or_excerpt(t):
                            title = t.get("arabic_title") or t.get("english_title")
                            if title:
                                return title
                            content = (t.get("arabic_content") or t.get("english_content") or "").strip().replace("\n", " ")
                            return content[:60] + ("..." if len(content) > 60 else "")

                        tadabbur_rows = [
                            (
                                t["id"],
                                t.get("display_author") or "Unknown Author",
                                (
                                    f"{t['surah']}:{t['start_ayah']}-{t['end_ayah']}"
                                    if t["start_ayah"] != t["end_ayah"]
                                    else f"{t['surah']}:{t['start_ayah']}"
                                ),
                                _tadabbur_title_or_excerpt(t),
                                t["status"],
                                t["surah"],
                                t["start_ayah"],
                                t["end_ayah"],
                            )
                            for t in tadabbur_data
                        ]
                        df_tadabbur = pd.DataFrame(
                            tadabbur_rows,
                            columns=["ID", "Display Author", "Verse Reference", "Title", "Status", "Surah", "Start Ayah", "End Ayah"],
                        )

                        admin_tadabbur_editor_key = "admin_tadabbur_table_editor"
                        st.data_editor(
                            df_tadabbur,
                            use_container_width=True,
                            column_order=["Display Author", "Verse Reference", "Title", "Status"],
                            column_config={
                                "Display Author": st.column_config.TextColumn("Display Author", disabled=True),
                                "Verse Reference": st.column_config.TextColumn("Verse Reference", disabled=True),
                                "Title": st.column_config.TextColumn("Title", disabled=True),
                                "Status": st.column_config.SelectboxColumn(
                                    "Status",
                                    help="Toggle to change tadabbur status",
                                    options=["pending", "approved", "rejected"],
                                ),
                            },
                            disabled=["Display Author", "Verse Reference", "Title"],
                            hide_index=True,
                            key=admin_tadabbur_editor_key,
                        )

                        edited_tadabbur_rows = st.session_state.get(admin_tadabbur_editor_key, {}).get("edited_rows", {})
                        if edited_tadabbur_rows:
                            process_tadabbur_status_changes(edited_tadabbur_rows, df_tadabbur, admin_tadabbur_editor_key)

                    with nuggets_tab:
                        st.write("List of Nuggets")
                        nugget_data = supabase.table("nuggets").select("*").execute().data

                        nugget_rows = [
                            (
                                n["id"],
                                n.get("display_author") or "Unknown Author",
                                n.get("arabic_text") or n["english_text"],
                                n["status"],
                            )
                            for n in nugget_data
                        ]
                        df_nugget = pd.DataFrame(
                            nugget_rows,
                            columns=["ID", "Display Author", "Text", "Status"],
                        )

                        admin_nugget_editor_key = "admin_nugget_table_editor"
                        st.data_editor(
                            df_nugget,
                            use_container_width=True,
                            column_order=["Display Author", "Text", "Status"],
                            column_config={
                                "Display Author": st.column_config.TextColumn("Display Author", disabled=True),
                                "Text": st.column_config.TextColumn("Text", disabled=True),
                                "Status": st.column_config.SelectboxColumn(
                                    "Status",
                                    help="Toggle to change nugget status",
                                    options=["pending", "approved", "rejected"],
                                ),
                            },
                            disabled=["Display Author", "Text"],
                            hide_index=True,
                            key=admin_nugget_editor_key,
                        )

                        edited_nugget_rows = st.session_state.get(admin_nugget_editor_key, {}).get("edited_rows", {})
                        if edited_nugget_rows:
                            process_nugget_status_changes(edited_nugget_rows, df_nugget, admin_nugget_editor_key)

            reset_timestamp = elevenlabs_user.subscription.next_character_count_reset_unix
            if reset_timestamp:
                renewal_date = datetime.fromtimestamp(reset_timestamp, tz=timezone.utc)
            else:
                renewal_date = "No renewal date available (e.g., custom enterprise or inactive plan)."

            el_remaining_credits = (
                elevenlabs_user.subscription.character_limit - elevenlabs_user.subscription.character_count
            )
            st.markdown(
                f"Your ElevenLabs credits remaining: *{el_remaining_credits}* "
                f"({elevenlabs_user.subscription.character_count} / {elevenlabs_user.subscription.character_limit}). "
                f"Renewal date: {renewal_date}"
            )

        # ---- user dashboard ----
        manage_articles_tab, create_article_tab, create_tadabbur_tab = st.tabs(
            ["Manage Articles", "Create Article", "Create Tadabbur"]
        )
        with manage_articles_tab:
            st.write("Manage Articles")
            articles_data = (
                supabase.table("articles")
                .select("*")
                .eq("author_id", st.session_state.get("user_id"))
                .execute()
                .data
            )
            articles = [
                (a["id"], a["article_author"], a[f"{a['language'].lower()}_title"], a["status"], "View", "Actions")
                for a in articles_data
            ]
            df_articles = pd.DataFrame(articles, columns=["ID", "Article Author", "Title", "Status", "Read Article", "Actions"])
            df_articles["Options"] = df_articles["ID"].apply(
                lambda aid: f"/Article_Reader?article_id={aid}"
            )
            st.data_editor(
                df_articles,
                width="stretch",
                column_order=["Article Author", "Title", "Status", "Options"],
                column_config={
                    "Article Author": st.column_config.TextColumn("Article Author", disabled=True),
                    "Title": st.column_config.TextColumn("Title", disabled=True),
                    "Status": st.column_config.TextColumn("Status", disabled=True),
                    "Options": st.column_config.LinkColumn(
                        "Configure",
                        display_text="Options",
                    ),
                },
                disabled=["Article Author", "Title"],
                hide_index=True,
                key="article_table_editor",
            )

        with create_article_tab:
            with st.expander("Create New Article", expanded=True):
                left, right = st.columns([3, 2])
                with left:
                    with st.form("create_article_form"):
                        article_language = st.selectbox("Article Language", ["English", "Arabic"], index=None)
                        article_title = st.text_input("Article Title", max_chars=40)
                        article_summary = st.text_area("Article Summary", max_chars=100)
                        write, pdf, word, txt = st.tabs(["Write", "PDF", "Word", "Text"])
                        with write:
                            article_content = st.text_area("Article Content", max_chars=10000)
                        with pdf:
                            uploaded_pdf = st.file_uploader("Upload PDF", type=["pdf"])
                            if uploaded_pdf:
                                article_content = extract_text(uploaded_pdf, "pdf")
                        with word:
                            uploaded_word = st.file_uploader("Upload Word Document", type=["docx", "doc"])
                            if uploaded_word:
                                article_content = extract_text(uploaded_word, "docx")
                        with txt:
                            uploaded_txt = st.file_uploader("Upload Text File", type=["txt"])
                            if uploaded_txt:
                                article_content = extract_text(uploaded_txt, "txt")

                        article_hashtags = st.multiselect("Article Tags", options=TAG_OPTIONS, max_selections=5)
                        article_image = st.file_uploader("Article Image", type=["jpg", "jpeg", "png"])
                        article_author = st.text_input("Article Author", value=st.session_state.get("username", "Unknown Author"))
                        author_unknown = st.checkbox("Author Unknown", value=False)
                        if author_unknown or article_author.strip() == "":
                            article_author = "Unknown Author"
                        article_read_time = calculate_reading_time(article_content)

                        if article_image:
                            image_bytes = article_image.getvalue()

                        with_audio = st.checkbox(
                        "Include AI-generated audio for this article?",
                        help="If selected, AI-generated audio will be added to your article within 30 days."
                        )

                        col1, col2, col3 = st.columns(3, gap="small")
                        with col1:
                            preview_submitted = st.form_submit_button("Update Preview", type="secondary")
                        with col2:
                            read_full_article = st.form_submit_button("Read Full Article", type="secondary")
                        with col3:
                            create_article_submitted = st.form_submit_button("Create Article", type="primary")

                        if preview_submitted:
                            st.session_state["preview_title"] = article_title
                            st.session_state["preview_summary"] = article_summary
                            st.session_state["preview_image"] = article_image
                            st.rerun()

                        if read_full_article:
                            if article_language == "Arabic":
                                article_data = {
                                    "arabic_title": article_title,
                                    "arabic_summary": article_summary,
                                    "arabic_content": article_content
                                }
                            else:
                                article_data = {
                                    "english_title": article_title,
                                    "english_summary": article_summary,
                                    "english_content": article_content
                                }
                            article_dialog(article_data)

                        if create_article_submitted:
                            if not article_title or not article_summary or not article_content or not article_language:
                                st.error("Please fill in all required fields (Language, Title, Summary, Content).")
                            elif len(article_content) > 10000:
                                st.error("Article content exceeds the maximum length of 10,000 characters.")
                            elif not article_image:
                                st.error("Please upload an article image.")
                            else:
                                try:
                                    status, image_url = upload_file_to_r2(
                                        article_image,
                                        f"{article_title.lower().replace(' ', '-')}.jpg",
                                        f"articles/{st.session_state['user_id']}/{article_title.lower().replace(' ', '-')}"
                                    )
                                except Exception as exc:
                                    status, image_url = None, None
                                    st.error(f"Image upload failed: {exc}")

                                if status is not None:
                                    with st.spinner("Uploading article..."):
                                        insert_data = {
                                            "slug": article_title.lower().replace(" ", "-"),
                                            f"{article_language.lower()}_title": article_title,
                                            f"{article_language.lower()}_summary": article_summary,
                                            f"{article_language.lower()}_content": article_content,
                                            "status": "pending",
                                            "featured_image_url": image_url if status == 200 else None,
                                            "reading_time_minutes": calculate_reading_time(article_content),
                                            "author_id": st.session_state["user_id"],
                                            "language": article_language,
                                            "article_author": article_author,
                                            "published_at": datetime.now(timezone.utc).isoformat(),
                                            "tags": article_hashtags,
                                            "with_audio": with_audio
                                            # audio later
                                        }
                                        try:
                                            response = supabase.table("articles").insert(insert_data).execute()
                                        except Exception as exc:
                                            response = None
                                            st.error(f"Failed to create article: {exc}")

                                    if response and response.data:
                                        st.success(f"Article '{article_title}' created successfully!")
                                    elif response is not None:
                                        st.error("Failed to create article!")
                with right:
                    if image_bytes:
                        image = base64.b64encode(image_bytes).decode()
                        background = f"data:image/png;base64,{image}"
                    else:
                        # Placeholder image
                        background = "https://placehold.co/1080x1920"

                    html = f"""
                    <style>
                    .hero-card {{
                        position: relative;
                        width: 100%;
                        aspect-ratio: 9 / 16;
                        border-radius: 20px;
                        overflow: hidden;

                        background-image:
                            linear-gradient(
                                to top,
                                rgba(0,0,0,.95),
                                rgba(0,0,0,.45),
                                rgba(0,0,0,.10)
                            ),
                            url('{background}');

                        background-size: cover;
                        background-position: center;
                        display: flex;
                        align-items: flex-end;
                        box-sizing: border-box;
                    }}

                    .content {{
                        padding: 35px;
                        color: white;
                        width: 100%;
                        box-sizing: border-box;
                    }}

                    .badge {{
                        display: inline-block;
                        background: rgba(255,255,255,.2);
                        backdrop-filter: blur(8px);
                        padding: 6px 14px;
                        border-radius: 999px;
                        font-size: 13px;
                        font-weight: 600;
                        margin-bottom: 18px;
                    }}

                    .title {{
                        font-size: 36px;
                        font-weight: 700;
                        line-height: 1.15;
                        margin-bottom: 18px;
                    }}

                    .summary {{
                        font-size: 12px;
                        color: rgba(255,255,255,.9);
                        line-height: 1.6;
                        margin-bottom: 30px;
                    }}

                    .meta {{
                        font-size: 15px;
                        color: rgba(255,255,255,.75);
                    }}
                    </style>

                    <div class="hero-card">
                        <div class="content">
                            <div class="title">
                                {article_title if article_title else "Your Article Title"}
                            </div>
                            <div class="summary">
                                {article_summary if article_summary else "Your article summary will appear here..."}
                            </div>
                            <div class="meta">
                                &nbsp;&nbsp; • &nbsp;&nbsp; {article_author if article_author else "Author"}
                            </div>
                            <div>
                                &nbsp;&nbsp; • &nbsp;&nbsp; ⏱ {article_read_time if article_read_time else "0"} minutes read
                            </div>
                        </div>
                    </div>
                    """

                    st.markdown(html, unsafe_allow_html=True)

                    excerpt = (article_content or "")[:180].strip().replace("\n", " ")
                    if excerpt:
                        st.markdown(
                            f"""
                            <div style="margin-top: 12px; padding: 12px; border-radius: 10px; background: #f8f9fa; border: 1px solid #e5e7eb;">
                                <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em; color: #6b7280; margin-bottom: 8px;">Article Preview</div>
                                <div style="font-size: 15px; line-height: 1.6; color: #374151;">{excerpt}</div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )
                    else:
                        st.write("Start writing to see a preview of your article content here.")

        with create_tadabbur_tab:
            with st.expander("Create New Tadabbur", expanded=True):
                st.caption("Share what a verse means to you — a personal reflection, not a scholarly interpretation.")

                vcol1, vcol2, vcol3 = st.columns(3)
                with vcol1:
                    tadabbur_surah = st.number_input("Surah", min_value=1, max_value=114, value=1, step=1, key="tadabbur_surah")

                surah_ayah_count = get_surah_ayah_count(int(tadabbur_surah))
                # Clamp stale session-state values before each widget renders -- switching
                # surah/start can otherwise leave a previous value outside the new bounds,
                # which Streamlit raises on rather than silently clamping.
                if st.session_state.get("tadabbur_start_ayah", 1) > surah_ayah_count:
                    st.session_state["tadabbur_start_ayah"] = surah_ayah_count

                with vcol2:
                    tadabbur_start_ayah = st.number_input(
                        "Start Ayah", min_value=1, max_value=surah_ayah_count, value=1, step=1, key="tadabbur_start_ayah"
                    )

                max_end_ayah = min(surah_ayah_count, int(tadabbur_start_ayah) + TADABBUR_MAX_VERSES - 1)
                current_end_ayah = st.session_state.get("tadabbur_end_ayah", int(tadabbur_start_ayah))
                if current_end_ayah < int(tadabbur_start_ayah) or current_end_ayah > max_end_ayah:
                    st.session_state["tadabbur_end_ayah"] = max(int(tadabbur_start_ayah), min(current_end_ayah, max_end_ayah))

                with vcol3:
                    tadabbur_end_ayah = st.number_input(
                        "End Ayah",
                        min_value=int(tadabbur_start_ayah),
                        max_value=max(max_end_ayah, int(tadabbur_start_ayah)),
                        value=int(tadabbur_start_ayah),
                        step=1,
                        key="tadabbur_end_ayah",
                    )

                with st.spinner("Loading verse preview..."):
                    preview_parts = []
                    for a in range(int(tadabbur_start_ayah), int(tadabbur_end_ayah) + 1):
                        text = fetch_ayah_text(int(tadabbur_surah), a)
                        if text:
                            preview_parts.append(f"({a}) {text}")

                if preview_parts:
                    st.markdown(
                        "<div dir='rtl' style='text-align:right; font-size:20px; line-height:2; padding:12px; "
                        "background:#f8f9fa; color:#374151; border:1px solid #e5e7eb; border-radius:10px;'>"
                        + " ".join(preview_parts)
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.info("No preview available for this verse range yet.")

                with st.form("create_tadabbur_form"):
                    tadabbur_language = st.selectbox("Language", ["English", "Arabic"], index=None, key="tadabbur_language")
                    tadabbur_title = st.text_input("Title (optional)", max_chars=40, key="tadabbur_title")
                    tadabbur_write, tadabbur_pdf_tab, tadabbur_word_tab, tadabbur_txt_tab = st.tabs(["Write", "PDF", "Word", "Text"])
                    with tadabbur_write:
                        tadabbur_content = st.text_area("Your Reflection", max_chars=10000, key="tadabbur_content")
                    with tadabbur_pdf_tab:
                        tadabbur_pdf = st.file_uploader("Upload PDF", type=["pdf"], key="tadabbur_pdf")
                        if tadabbur_pdf:
                            tadabbur_content = extract_text(tadabbur_pdf, "pdf")
                    with tadabbur_word_tab:
                        tadabbur_word = st.file_uploader("Upload Word Document", type=["docx", "doc"], key="tadabbur_word")
                        if tadabbur_word:
                            tadabbur_content = extract_text(tadabbur_word, "docx")
                    with tadabbur_txt_tab:
                        tadabbur_txt = st.file_uploader("Upload Text File", type=["txt"], key="tadabbur_txt")
                        if tadabbur_txt:
                            tadabbur_content = extract_text(tadabbur_txt, "txt")

                    tadabbur_tags = st.multiselect("Tags", options=TAG_OPTIONS, max_selections=5, key="tadabbur_tags")
                    tadabbur_image = st.file_uploader("Image (optional)", type=["jpg", "jpeg", "png"], key="tadabbur_image")
                    tadabbur_display_author = st.text_input(
                        "Display Author", value=st.session_state.get("username", "Unknown Author"), key="tadabbur_author_input"
                    )
                    tadabbur_author_unknown = st.checkbox("Author Unknown", value=True, key="tadabbur_author_unknown")
                    if tadabbur_author_unknown or tadabbur_display_author.strip() == "":
                        tadabbur_display_author = "Unknown Author"

                    create_tadabbur_submitted = st.form_submit_button("Create Tadabbur", type="primary")

                    if create_tadabbur_submitted:
                        verse_count = int(tadabbur_end_ayah) - int(tadabbur_start_ayah) + 1
                        if not tadabbur_language or not tadabbur_content:
                            st.error("Please select a language and write your reflection.")
                        elif len(tadabbur_content) > 10000:
                            st.error("Reflection exceeds the maximum length of 10,000 characters.")
                        elif tadabbur_end_ayah < tadabbur_start_ayah:
                            st.error("End ayah must be greater than or equal to start ayah.")
                        elif verse_count > TADABBUR_MAX_VERSES:
                            st.error(f"Please select a range of {TADABBUR_MAX_VERSES} verses or fewer.")
                        else:
                            tadabbur_slug = generate_tadabbur_slug(
                                tadabbur_title, int(tadabbur_surah), int(tadabbur_start_ayah), int(tadabbur_end_ayah)
                            )

                            tadabbur_image_url = None
                            if tadabbur_image:
                                try:
                                    img_status, img_result = upload_file_to_r2(
                                        tadabbur_image,
                                        f"{tadabbur_slug}.jpg",
                                        f"tadabbur/{st.session_state['user_id']}/{tadabbur_slug}",
                                    )
                                    if img_status == 200:
                                        tadabbur_image_url = img_result
                                    else:
                                        st.warning(f"Image upload failed, continuing without an image: {img_result}")
                                except Exception as exc:
                                    st.warning(f"Image upload failed, continuing without an image: {exc}")

                            with st.spinner("Submitting tadabbur..."):
                                tadabbur_insert_data = {
                                    "slug": tadabbur_slug,
                                    "surah": int(tadabbur_surah),
                                    "start_ayah": int(tadabbur_start_ayah),
                                    "end_ayah": int(tadabbur_end_ayah),
                                    f"{tadabbur_language.lower()}_title": tadabbur_title.strip() if tadabbur_title.strip() else None,
                                    f"{tadabbur_language.lower()}_content": tadabbur_content,
                                    "status": "pending",
                                    "featured_image_url": tadabbur_image_url,
                                    "reading_time_minutes": calculate_reading_time(tadabbur_content),
                                    "author_id": st.session_state["user_id"],
                                    "display_author": tadabbur_display_author,
                                    "tags": tadabbur_tags,
                                    "language": tadabbur_language,
                                }
                                try:
                                    tadabbur_response = supabase.table("tadabbur").insert(tadabbur_insert_data).execute()
                                except Exception as exc:
                                    tadabbur_response = None
                                    st.error(f"Failed to submit tadabbur: {exc}")

                            if tadabbur_response and tadabbur_response.data:
                                st.success("Tadabbur submitted successfully!")
                            elif tadabbur_response is not None:
                                st.error("Failed to submit tadabbur!")

        if st.button("Logout"):
            supabase.auth.sign_out()
            st.session_state["logged_in"] = False
            st.session_state["user_email"] = None
            st.session_state["user_id"] = None
            st.session_state["username"] = None
            cookies["refresh_token"] = ""
            cookies.save()
            st.rerun()
else:
    def fetch_article_content(article_id: str):
        """Fetch full article content from Supabase using ID."""
        res = (
            supabase.table("articles")
            .select("*")
            .eq("id", article_id)
            .execute()
        )
        return res.data[0] if res.data else None

    if article_id:
        article = fetch_article_content(article_id)
        if article:
            lang = article["language"]
            title = article.get(f"{lang.lower()}_title", "Untitled Article")
            summary = article.get(f"{lang.lower()}_summary", "No Summary available.")
            content = article.get(f"{lang.lower()}_content", "No content available.")

            st.title(title)
            st.subheader(summary)
            st.write(f"Created by {article.get('article_author', 'Unknown Author')}")
            st.caption(f"Article ID: `{article['id']}`")

            download_col, edit_col, delete_col = st.columns([1, 1, 1])
            with download_col:
                st.download_button(
                    label="📥 Download as .txt file",
                    data=f"{title}\n\n{content}",
                    file_name=f"{title.lower().replace(' ', '_')}.txt",
                    mime="text/plain",
                    type="primary",
                )

            with edit_col:
                if st.button("✏️ Edit Article"):
                    edit_article_dialog(article)

            with delete_col:
                if st.button("🗑 Delete Article"):
                    try:
                        supabase.table("articles").delete().eq("id", article_id).execute()
                        delete_file_from_r2(
                            f"{title.lower().replace(' ', '-')}.jpg",
                            f"articles/{article['author_id']}/{title.lower().replace(' ', '-')}"
                        )
                        st.success(f"Article '{title}' deleted successfully!")
                        st.write("Redirecting to the main page...")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed to delete article: {exc}")

            st.divider()
            st.markdown(content)
        else:
            st.error("Article not found in database.")
    else:
        st.warning("No article ID provided in URL.")