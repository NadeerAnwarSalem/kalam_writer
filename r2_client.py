"""
r2_client.py
------------
Uploads audio files to Cloudflare R2 (public bucket) and returns the public
URL to store in Supabase.

Setup (Cloudflare dashboard):
  1. R2 > Create bucket.
  2. Bucket Settings > Public Access > enable the r2.dev subdomain (or attach
     your own custom domain). Copy that base URL.
  3. R2 > Manage API Tokens > create a token with Object Read & Write for
     this bucket. You'll get an Access Key ID + Secret Access Key.
  4. Your Account ID is shown on the R2 overview page.

Install once: pip install boto3

Environment variables needed:
  R2_ACCOUNT_ID
  R2_ACCESS_KEY_ID
  R2_SECRET_ACCESS_KEY
  R2_BUCKET_NAME
  R2_PUBLIC_BASE_URL   e.g. https://pub-xxxxxxxx.r2.dev  (no trailing slash)
"""

import os
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


def get_r2_client():
    account_id = "26b2e1d76ead46ae8a1efad2fbc6f4cb"
    access_key = "266db619613c01a294421e43d4924a5f"

    secret_key = "ad348e5dc73d47b99e44d6a24530fe373455a542d3211103213fde955d3842af"

    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_audio(local_path: str, key: str, content_type: str = "audio/mpeg") -> str:
    """Uploads a local file to R2 under `key` (e.g. '2/222.mp3') and returns
    the public URL it will be reachable at."""
    client = get_r2_client()
    bucket = "kalam"
    base_url = "https://pub-4ef637e0a95f4fe88fc0dacd69c3602a.r2.dev".rstrip("/")

    client.upload_file(
        local_path,
        bucket,
        key,
        ExtraArgs={"ContentType": content_type},
    )

    return f"{base_url}/{key}"


def object_exists(key: str) -> bool:
    """Check if a file is already uploaded, so you don't re-upload/re-pay
    for regeneration unnecessarily."""
    client = get_r2_client()
    bucket = _require_env("R2_BUCKET_NAME")
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False
