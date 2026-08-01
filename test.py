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

import os
from supabase import create_client, Client

url: str = os.environ.get("SUPABASE_URL")
key: str = os.environ.get("SUPABASE_KEY")
service_key: str = os.environ.get("SUPABASE_SERVICE_KEY")

# Tip: Use service_key for batch updates to bypass Row Level Security (RLS) if needed
supabase: Client = create_client(url, "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp4amZxd3RueHh2ZGNnemZyd29vIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTQxODA1MiwiZXhwIjoyMTAwOTk0MDUyfQ.ZU0b8El9DfiXNQrxrmf49lggH-YJltEkIGhjPojwkMY")

# 1. Fetch both the primary key ('id') and 'audio_url'
response = supabase.table("ayat").select("*").execute()

# 2. Modify the URLs while preserving the 'id'
updated_data = [
    {**item, 'audio_url': item['audio_url'].replace('.r2.dev/', '.r2.dev/ayat/')}
    for item in response.data
    if item.get('audio_url')  # Safety check in case audio_url is None
]

# 3. Upsert using the primary key to perform an in-place update
if updated_data:
    supabase.table("ayat").upsert(updated_data).execute()
    print(f"Successfully updated {len(updated_data)} rows!")