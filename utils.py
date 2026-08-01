import math
import re
import base64
import io
from pathlib import Path
import PyPDF2
from docx import Document
import boto3
import os

R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME")
R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL")
r2_client = boto3.client(
    service_name='s3',
    endpoint_url=f'https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com',
    aws_access_key_id=R2_ACCESS_KEY_ID,
    aws_secret_access_key=R2_SECRET_ACCESS_KEY,
    region_name='auto' 
)

def upload_file_to_r2(uploaded_file, file_name, directory):
    full_path = f"{directory.strip('/')}/{file_name}"
    try:
        r2_client.upload_fileobj(uploaded_file, R2_BUCKET_NAME, full_path)
        return 200, f"{R2_PUBLIC_BASE_URL}/{full_path}"
    except Exception as e:
        return 500, "Error uploading file to R2: " + str(e)




def extract_text_from_pdf(uploaded_file) -> str:
    """Extract text from PDF file."""
    if PyPDF2 is None:
        raise ImportError("PyPDF2 is required to extract text from PDF files")
    
    pdf_reader = PyPDF2.PdfReader(io.BytesIO(uploaded_file.getvalue()))
    text = ""
    for page in pdf_reader.pages:
        text += page.extract_text() + "\n"
    return text.strip()


def extract_text_from_word(uploaded_file) -> str:
    """Extract text from Word (.docx) file."""
    if Document is None:
        raise ImportError("python-docx is required to extract text from Word files")
    
    doc = Document(io.BytesIO(uploaded_file.getvalue()))
    text = "\n".join([para.text for para in doc.paragraphs])
    return text.strip()


def extract_text_from_txt(uploaded_file) -> str:
    """Extract text from plain text file."""
    return uploaded_file.getvalue().decode('utf-8').strip()


def extract_text(uploaded_file, file_type: str) -> str:
    """Extract text from various file formats (pdf, docx, txt)."""
    file_type = file_type.lower()
    
    if file_type == "pdf":
        text = extract_text_from_pdf(uploaded_file)
        return text if len(text) > 0 and len(text) < 10000 else "Text too long or empty."
    elif file_type in ["docx", "doc"]:
        text = extract_text_from_word(uploaded_file)
        return text if len(text) > 0 and len(text) < 10000 else "Text too long or empty."
    elif file_type == "txt":
        text = extract_text_from_txt(uploaded_file)
        return text if len(text) > 0 and len(text) < 10000 else "Text too long or empty."
    else:
        raise ValueError(f"Unsupported file type: {file_type}")



def image_to_base64(uploaded_file):
    """Convert uploaded image to base64 for HTML."""
    return base64.b64encode(uploaded_file.getvalue()).decode()

def calculate_reading_time(text: str, wpm: int = 200, image_count: int = 0) -> str:
    # Remove HTML tags if processing raw markup
    clean_text = re.sub(r'<[^>]+>', '', text)
    words = len(re.findall(r'\w+', clean_text))
    
    total_seconds = (words / wpm) * 60
    
    # Calculate image duration
    for i in range(1, image_count + 1):
        total_seconds += max(12 - (i - 1), 3)
        
    minutes = math.ceil(total_seconds / 60)
    return minutes