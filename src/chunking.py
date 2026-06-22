"""Чанкування тексту законів для LLM-аналізу.

Чанки ріжуться на межах речень/абзаців, щоб LLM отримував змістовно цілісні блоки.
"""
import re

CHUNK_SIZE = 60000  # символів на чанк (126K context window)


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Розбити текст на чанки з оверхедом до кінця речення/абзацу.

    Алгоритм:
    1. Шукаємо позицію chunk_size в тексті
    2. Відступаємо назад до найближчого абзацу (\n\n) або речення (. ! ?)
    3. Ріжемо там
    4. Якщо абзац/речення довше chunk_size — ріжемо жорстко

    Args:
        text: Повний текст закону.
        chunk_size: Цільовий розмір чанка в символах.

    Returns:
        Список чанків тексту.
    """
    if not text or not text.strip():
        return []

    text = text.strip()

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end >= len(text):
            chunks.append(text[start:])
            break

        # Шукаємо найближчу межу абзацу назад від end
        paragraph_break = text.rfind("\n\n", start + chunk_size // 2, end)
        if paragraph_break > start:
            end = paragraph_break + 2
            chunks.append(text[start:end].strip())
            start = end
            continue

        # Шукаємо кінці речення назад від end
        sentence_break = -1
        for pattern in [". ", "! ", "? ", ".\n", "!\n", "?\n"]:
            pos = text.rfind(pattern, start + chunk_size // 2, end)
            if pos > sentence_break:
                sentence_break = pos

        if sentence_break > start:
            end = sentence_break + 2
            chunks.append(text[start:end].strip())
            start = end
            continue

        # Якщо не знайшли межу — шукаємо хоча б пробіл
        space_break = text.rfind(" ", start + chunk_size // 2, end)
        if space_break > start:
            end = space_break + 1

        chunks.append(text[start:end].strip())
        start = end

    return [c for c in chunks if c]
