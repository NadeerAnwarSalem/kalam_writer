import os
import pandas as pd

from utils import *
from supabase import create_client, Client

url: str = st.secrets.get("SUPABASE_URL")
key: str = st.secrets.get("SUPABASE_KEY")
service_key: str = st.secrets.get("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(url, key)
supabase_admin: Client = create_client(url, service_key)

image_bytes = None  # Initialize image_bytes to None

def update_article_status(article_id: str, new_status: bool):
    """Update a specific article's status in Supabase."""
    supabase.table("articles").update({"status": new_status}).eq(
        "id", article_id
    ).execute()

article_id = st.query_params.get("article_id")

if not article_id:
    if not st.session_state.get("logged_in"):
        with st.form("login_form"):
            st.write("Login Form")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")

            if submitted:
                response = supabase.auth.sign_in_with_password(
                    {
                        "email": email,
                        "password": password,
                    }
                )

                if response.user:
                    st.success("Login successful!")
                    st.session_state["logged_in"] = True
                    st.session_state["user_email"] = response.user.email
                    st.session_state["user_id"] = response.user.id
                    st.session_state["username"] = response.user.user_metadata.get("username", "")
                    st.session_state["user_role"] = response.user.app_metadata.get("role", "user")
                    st.rerun()
                else:
                    st.error(f"Login failed!")
    else:
        #admin dashboard code here
        if st.session_state.get("user_role") == "admin":
            st.write(f"Welcome, {st.session_state['username']}! (Admin)")
            if st.button("refresh"):
                st.rerun()
            with st.expander("Create User"):
                with st.form("create_user_form"):
                    st.write("Create User Form")
                    new_email = st.text_input("New User Email")
                    new_password = st.text_input("New User Password", type="password")
                    new_username = st.text_input("New User Username")
                    new_role = st.selectbox("New User Role", ["user", "admin"])
                    create_submitted = st.form_submit_button("Create User")

                    if create_submitted:
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
                        if response.user:
                            st.success(f"User {new_email} created successfully!")
                        else:
                            st.error(f"Failed to create user!")

            with st.container(border=True, width="stretch", height=500):
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
                    articles = [(article["id"], article["article_author"], article["title"], article["status"], "View") for article in articles_data]
                    df_articles = pd.DataFrame(articles, columns=["ID", "Article Author", "Title", "Status", "Actions"])  
                    df_articles["Actions"] = df_articles["ID"].apply(
                        lambda article_id: f"/Article_Reader?article_id={article_id}"
                    )
                    edited_df = st.data_editor(df_articles, column_order=["Article Author", "Title", "Status", "Actions"], column_config={
                    "Article Author": st.column_config.TextColumn("Article Author", disabled=True),
                    "Title": st.column_config.TextColumn("Title", disabled=True),
                    "Status": st.column_config.SelectboxColumn(
                        "Status",
                        help="Toggle to change article status",
                        options=["pending", "approved", "rejected"],
                    ),
                    "Actions": st.column_config.LinkColumn(
                        "Read Article",
                        display_text="Read Article",
                    ),
                    },
                    disabled=["Article Author", "Title"],  # Prevent editing ID and Name
                    hide_index=True,
                    key="article_table_editor",)  

                    changes = st.session_state.get("article_table_editor", {}).get(
                        "edited_rows", {}
                    )

                    if changes:
                        for index, change in changes.items():
                            if "Status" in change:
                                article_id = df_articles.iloc[index]["ID"] 
                                new_status = change["Status"]
                                update_article_status(article_id, new_status)
                        
                        st.toast("Article statuses updated successfully!")
        else:
            #user dashboard code here
            st.write(f"Welcome, {st.session_state['username']}!")
            with st.expander("Create New Article"):
                left, right = st.columns([3, 2])
                with left:
                    with st.form("create_article_form"):
                        article_language = st.selectbox("Article Language", ["English", "Arabic"])
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
                        article_image = st.file_uploader("Article Image", type=["jpg", "jpeg", "png"])
                        article_author = st.text_input("Article Author", value=st.session_state.get("username", "Unknown Author"))
                        article_read_time = calculate_reading_time(article_content)

                        if article_image:
                            image_bytes = article_image.getvalue()

                        col1, col2 = st.columns(2, gap="small")
                        with col1:
                            preview_submitted = st.form_submit_button("Update Preview")
                        with col2:
                            create_article_submitted = st.form_submit_button("Create Article")
                        
                        if preview_submitted:
                            st.session_state["preview_title"] = article_title
                            st.session_state["preview_summary"] = article_summary
                            st.session_state["preview_image"] = article_image
                            st.rerun()

                        if create_article_submitted:
                            if not article_title or not article_summary or not article_content:
                                st.error("Please fill in all required fields (Title, Summary, Content).")
                            elif len(article_content) > 10000:
                                st.error("Article content exceeds the maximum length of 10,000 characters.")
                            elif not article_image:
                                st.error("Please upload an article image.")
                            else:
                                status, image_url = upload_file_to_r2(
                                    article_image,
                                    f"{article_title.lower().replace(' ', '-')}.jpg",
                                    f"articles/{st.session_state['user_id']}/{article_title.lower().replace(' ', '-')}"
                                )
                                response = supabase.table("articles").insert(
                                    {
                                        "slug": article_title.lower().replace(" ", "-"),
                                        "title": article_title,
                                        "summary": article_summary,
                                        "content": article_content,
                                        "status": "pending",
                                        "featured_image_url": image_url if status == 200 else None,
                                        "reading_time_minutes": calculate_reading_time(article_content),
                                        "author_id": st.session_state["user_id"],
                                        "language": article_language,
                                        "article_author": article_author,
                                    }
                                ).execute()

                                if response.data:
                                    st.success(f"Article '{article_title}' created successfully!")
                                else:
                                    st.error("Failed to create article!")
                with right:
                    
                    if image_bytes:
                        image = base64.b64encode(image_bytes).decode()
                        background = f"data:image/png;base64,{image}"
                    else:
                        # Placeholder image
                        background = "https://images.unsplash.com/photo-1518770660439-4636190af475?w=1200"

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
            st.rerun()
else:
    def fetch_article_content(article_id: str):
        """Fetch full article content from Supabase using ID."""
        res = (
            supabase.table("articles")
            .select("id, title, content, created_at, article_author")
            .eq("id", article_id)
            .execute()
        )
        return res.data[0] if res.data else None


    # 1. Read URL Query Parameter
    article_id = st.query_params.get("article_id")

    if article_id:
        # 2. Fetch full article data from Supabase
        article = fetch_article_content(article_id)

        if article:
            title = article.get("title", "Untitled Article")
            content = article.get("content", "No content available.")

            # Header section
            st.title(title)
            st.write(f"Created by {article.get('article_author', 'Unknown Author')}")
            
            st.caption(f"Article ID: `{article['id']}`")

            # Top Action Bar: Download Button
            st.download_button(
                label="📥 Download as .txt file",
                data=f"{title}\n\n{content}",  # Text file content payload
                file_name=f"{title.lower().replace(' ', '_')}.txt",
                mime="text/plain",
                type="primary",
            )

            st.divider()

            # 3. Read Article on screen
            st.markdown(content)

        else:
            st.error("Article not found in database.")
    else:
        st.warning("No article ID provided in URL.")