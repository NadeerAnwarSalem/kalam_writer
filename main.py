import pandas as pd
import random
import tempfile

from datetime import datetime, timezone
from utils import *
from supabase import create_client, Client
from tag_options import TAG_OPTIONS
from streamlit_cookies_manager import EncryptedCookieManager
from articles_translate_supabase import translate_article, other_language
from elevenlabs import ElevenLabs
from r2_client import upload_audio


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
                    users_tab, articles_tab = st.tabs(["Users", "Articles"])
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
                                        st.write(f"This article will use approximately {selected_length} credits when narrated.")

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
        manage_articles_tab, create_article_tab = st.tabs(["Manage Articles", "Create Article"])
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