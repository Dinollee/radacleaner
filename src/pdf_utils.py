"""Завантаження PDF з RADA API, витягування тексту та чанкування."""
import hashlib
import json
import logging
import os
import re
import time
import urllib.request

log = logging.getLogger(__name__)


def get_rada_token(max_retries: int = 3) -> str:
    """Отримує токен для RADA API з retry."""
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request("https://data.rada.gov.ua/api/token")
            req.add_header("User-Agent", "Mozilla/5.0")
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())["token"]
        except Exception as e:
            if attempt < max_retries - 1:
                wait = 2 ** attempt * 3
                log.warning("RADA token fetch failed (attempt %d/%d): %s, retry in %ds",
                            attempt + 1, max_retries, str(e)[:100], wait)
                time.sleep(wait)
            else:
                raise


def download_rada_pdf(file_id: str, token: str | None = None, max_retries: int = 3) -> bytes:
    """Завантажує PDF з RADA API по file_id з підтримкою чанкування та retry.

    Args:
        file_id: Ідентифікатор файлу на RADA.
        token: RADA API токен (якщо None — отримує новий).
        max_retries: Максимальна кількість спроб при помилках сервера.

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
        for attempt in range(max_retries):
            try:
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
                    break  # success, exit retry loop
            except urllib.error.HTTPError as e:
                if e.code in (503, 429, 500) and attempt < max_retries - 1:
                    wait = 2 ** attempt * 5  # 5s, 10s, 20s
                    log.warning("RADA 503/429/500 for file_id=%s chunk=%d, retry %d/%d wait=%ds",
                                file_id, chunk, attempt + 1, max_retries, wait)
                    time.sleep(wait)
                else:
                    raise

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


RECURSIVE_SEPARATORS = ["\n\n", "\n", " ", ""]


def chunk_text(text: str, max_size: int = 600, overlap: int = 100) -> list[str]:
    """Рекурсивний чанкинг тексту з overlap.

    Ієрархія роздільників: \\n\\n → \\n → пробіл → символ.
    Кожен чанк намагається закінчитись на межі абзацу/рядка/слова.
    Overlap забезпечує зв'язність між сусідніми чанками.

    Args:
        text: Вхідний текст.
        max_size: Максимальний розмір чанку в символах.
        overlap: Кількість символів overlap між чанками.

    Returns:
        Список чанків тексту.
    """
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    text = text.strip()

    if not text:
        return []

    if len(text) <= max_size:
        return [text]

    return _recursive_split(text, max_size, overlap)


def _recursive_split(text: str, max_size: int, overlap: int) -> list[str]:
    """Рекурсивно ділить текст, намагаючись знайти красивий стик."""
    if len(text) <= max_size:
        return [text.strip()] if text.strip() else []

    # Шукаємо роздільник, який є в тексті
    separator = ""
    for sep in RECURSIVE_SEPARATORS:
        if sep in text:
            separator = sep
            break

    # Якщо немає жодного роздільника — ріжемо по max_size
    if not separator:
        cut = max_size
        chunk = text[:cut].strip()
        rest = text[max(0, cut - overlap):].strip()
        result = []
        if chunk:
            result.append(chunk)
        if rest:
            result.extend(_recursive_split(rest, max_size, overlap))
        return result

    parts = text.split(separator)
    chunks: list[str] = []
    current = ""

    for part in parts:
        candidate = (current + separator + part) if current else part

        if len(candidate) <= max_size:
            current = candidate
        else:
            if current.strip():
                chunks.append(current.strip())

            # Якщо сам part більший за max_size — рекурсивно ділимо
            if len(part) > max_size:
                sub_chunks = _recursive_split(part, max_size, overlap)
                chunks.extend(sub_chunks)
                current = ""
            else:
                # overlap: беремо кінець попереднього чанка
                if current and overlap > 0:
                    tail = current[-overlap:]
                    # Знаходимо межу слова в overlap-вікні
                    space_idx = tail.find(" ") if " " in tail else -1
                    if space_idx >= 0:
                        tail = tail[space_idx + 1:]
                    current = tail + separator + part
                else:
                    current = part

    if current.strip():
        chunks.append(current.strip())

    return [c for c in chunks if c]


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