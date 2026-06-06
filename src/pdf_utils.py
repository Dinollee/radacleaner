"""Завантаження PDF з RADA API, витягування тексту та чанкування."""
import hashlib
import json
import logging
import os
import re
import urllib.request

log = logging.getLogger(__name__)


def get_rada_token() -> str:
    """Отримує токен для RADA API."""
    req = urllib.request.Request("https://data.rada.gov.ua/api/token")
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())["token"]


def download_rada_pdf(file_id: str, token: str | None = None) -> bytes:
    """Завантажує PDF з RADA API по file_id з підтримкою чанкування.

    Args:
        file_id: Ідентифікатор файлу на RADA.
        token: RADA API токен (якщо None — отримує новий).

    Returns:
        Бінарний вміст PDF.
    """
    if token is None:
        token = get_rada_token()

    base = "https://itd.rada.gov.ua/billinfo/api/file/download/"
    all_data: list[bytes] = []
    chunk = 0
    total_size: int | None = None

    while True:
        req = urllib.request.Request(
            base + f"?id={file_id}",
            headers={
                "User-Agent": token,
                "X-File-Id": str(file_id),
                "X-Current-Chunk": str(chunk),
                "Referer": f"https://itd.rada.gov.ua/billInfo/Bills/pubFile/{file_id}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if chunk == 0:
                total_size = int(resp.headers.get("Size", "0"))
            all_data.append(data)

            if total_size and sum(len(d) for d in all_data) >= total_size:
                break
            if len(data) == 0:
                break
            chunk += 1
            if chunk > 200:
                log.warning("Too many chunks for file_id=%s, stopping", file_id)
                break

    return b"".join(all_data)


def extract_pdf_text(filepath: str) -> str:
    """Витягує текст із PDF файлу за допомогою PyMuPDF.

    Args:
        filepath: Шлях до PDF файлу.

    Returns:
        Текст, витягнутий з PDF.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(filepath)
    text = "".join(page.get_text() for page in doc)
    doc.close()
    return text


def chunk_text(text: str, max_size: int = 600) -> list[str]:
    """Розбиває текст на смислові чанки.

    Args:
        text: Вхідний текст.
        max_size: Максимальний розмір чанку в символах.

    Returns:
        Список чанків тексту.
    """
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    paragraphs = [p.strip() for p in text.split("\n") if p.strip() and len(p.strip()) > 15]

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) < max_size:
            current = (current + "\n" + para) if current else para
        else:
            if current:
                chunks.append(current.strip())
            current = para
    if current and len(current) > 30:
        chunks.append(current.strip())
    return chunks


def determine_doc_type(doc_name: str) -> str:
    """Визначає тип документа за назвою."""
    if "Закону" in doc_name:
        return "zakon"
    elif "Пояснювальна" in doc_name:
        return "poyasn"
    return "other"


def classify_chunk_section(text: str) -> str:
    """Класифікує секцію чанку за змістом."""
    prefix = text[:200].lower()
    if "метою" in prefix or "мета" in prefix:
        return "meta"
    elif any(w in prefix for w in ["фінансування", "бюджет", "витрат"]):
        return "finance"
    return "general"


def md5_hash(data: bytes) -> str:
    """MD5 хеш для перевірки змін версій документа."""
    return hashlib.md5(data).hexdigest()