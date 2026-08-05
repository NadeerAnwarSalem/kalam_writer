# import streamlit as st 
# import os

# from supabase import create_client, Client

# url: str = os.environ.get("SUPABASE_URL")
# key: str = os.environ.get("SUPABASE_KEY")
# supabase_admin = create_client(url, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp4amZxd3RueHh2ZGNnemZyd29vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTQxODA1MiwiZXhwIjoyMTAwOTk0MDUyfQ.ZU0b8El9DfiXNQrxrmf49lggH-YJltEkIGhjPojwkMY")
# response = supabase_admin.auth.admin.create_user(
#     {
#         "email": "nadeer@kalam.com",
#         "password": "number.nine1",
#         "email_confirm": True,
#         "user_metadata": {
#             "username": "Nadeer Salem"
#         },
#         "app_metadata": {
#             "role": "admin"
#         }
#     }
# )

# -----------------------------------------------------------------------

# import boto3

# # --- CONFIGURATION ---
# ACCOUNT_ID = "26b2e1d76ead46ae8a1efad2fbc6f4cb"
# ACCESS_KEY_ID = "2721ebeb3dcbe4766bf0e8bbf8191c69"
# SECRET_ACCESS_KEY = "20c913584d78369000129b440da17d0dfd775a5ad1ac0b7bd84aa639feee8ee6"
# BUCKET_NAME = "kalam"

# # Folder where old files will be moved (include trailing slash)
# TARGET_FOLDER = "ayat/" 
# # ---------------------

# # Initialize S3 client for Cloudflare R2
# s3 = boto3.client(
#     "s3",
#     endpoint_url=f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com",
#     aws_access_key_id=ACCESS_KEY_ID,
#     aws_secret_access_key=SECRET_ACCESS_KEY,
#     region_name="auto"
# )

# def move_files_to_folder():
#     paginator = s3.get_paginator("list_objects_v2")
    
#     # Track how many files are moved
#     moved_count = 0

#     print("Starting migration process...\n")

#     # Iterate through all objects in the bucket
#     for page in paginator.paginate(Bucket=BUCKET_NAME):
#         if "Contents" not in page:
#             print("No objects found in the bucket.")
#             return

#         for obj in page["Contents"]:
#             old_key = obj["Key"]

#             # Skip files that are already inside the target directory or other subfolders you want to ignore
#             if old_key.startswith(TARGET_FOLDER) or old_key.startswith("articles/"):
#                 continue

#             new_key = f"{TARGET_FOLDER}{old_key}"

#             print(f"Moving: {old_key}  -->  {new_key}")

#             # 1. Copy object to new key location
#             s3.copy_object(
#                 Bucket=BUCKET_NAME,
#                 CopySource={"Bucket": BUCKET_NAME, "Key": old_key},
#                 Key=new_key
#             )

#             # 2. Delete original object from old key location
#             s3.delete_object(
#                 Bucket=BUCKET_NAME,
#                 Key=old_key
#             )

#             moved_count += 1

#     print(f"\nFinished! Total files moved: {moved_count}")

# ---------------------------------------------------------------

# import os
# from datetime import datetime, timezone
# from supabase import create_client, Client

# url: str = os.environ.get("SUPABASE_URL")
# key: str = os.environ.get("SUPABASE_KEY")
# service_key: str = os.environ.get("SUPABASE_SERVICE_KEY")

# # Tip: Use service_key for batch updates to bypass Row Level Security (RLS) if needed
# supabase: Client = create_client(url, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp4amZxd3RueHh2ZGNnemZyd29vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTQxODA1MiwiZXhwIjoyMTAwOTk0MDUyfQ.ZU0b8El9DfiXNQrxrmf49lggH-YJltEkIGhjPojwkMY")

# # 1. Fetch the primary key ('id') and the published_at field
# response = supabase.table("articles").select("*").execute()

# # 2. Fill published_at with current time for rows where it is missing
# current_time = datetime.now(timezone.utc).isoformat()
# updated_data = [
#     {**item, 'published_at': current_time}
#     for item in (response.data or [])
#     if item.get('published_at') is None
# ]

# # 3. Upsert using the primary key to perform an in-place update
# if updated_data:
#     supabase.table("articles").upsert(updated_data).execute()
#     print(f"Successfully updated {len(updated_data)} rows!")

# ---------------------------------------
# import os
# from bs4 import BeautifulSoup
# from supabase import create_client, Client


# # -----------------------------
# # Supabase
# # -----------------------------

# url = os.environ["SUPABASE_URL"]
# key = os.environ["SUPABASE_KEY"]

# supabase: Client = create_client(url, key)

# data = (
#     supabase
#     .table("ayat")
#     .select("surah, ayah_number, arabic_text, full_description")
#     .execute()
#     .data
# )


# # -----------------------------
# # Helpers
# # -----------------------------

# def clean_text(text):
#     if not text:
#         return ""

#     # Remove HTML
#     text = BeautifulSoup(
#         str(text),
#         "html.parser"
#     ).get_text("\n", strip=True)

#     return text.strip()


# def save_txt(path, text):
#     os.makedirs(
#         os.path.dirname(path),
#         exist_ok=True
#     )

#     with open(
#         path,
#         "w",
#         encoding="utf-8"
#     ) as f:
#         f.write(text)


# # -----------------------------
# # Generate TXT files
# # -----------------------------

# BASE_FOLDER = r"C:\Users\nadee\Desktop\Descriptions"

# for ayah in data:

#     surah = ayah["surah"]
#     ayah_number = ayah["ayah_number"]

#     arabic_text = clean_text(ayah["arabic_text"])
#     full_description = clean_text(ayah["full_description"])

#     file_path = os.path.join(
#         BASE_FOLDER,
#         str(surah),
#         f"{ayah_number}.txt"
#     )

#     save_txt(
#         file_path,
#         f"{arabic_text}\n\n{full_description}"
#     )

#     print(f"Created {file_path}")


import os
from ollama import Client

client = Client(
    host="https://ollama.com",
    headers={"Authorization": f"Bearer f96fdccfc81841a1a6390b84500e47fb.GkyTRBVTIK3KQpCDBW3aIv_U"},
)
OLLAMA_MODEL = "gemma4:31b-cloud"

response = client.chat(
    model=OLLAMA_MODEL,
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.message.content)