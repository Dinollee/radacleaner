"""Збереження та отримання оцінок ризиків з D1 (через Worker API)."""
import json
import logging

from .config import log
from .d1_client import d1_query, d1_exec

# Мапа severity → вага (для розрахунку overall_score)
SEVERITY_WEIGHTS = {"High": 3, "Medium": 2, "Low": 1}


def get_stored_hash(bill_id: int) -> str | None:
    """Повертає content_hash з rag_documents для bill_id або None."""
    rows = d1_query(
        "SELECT content_hash FROM rag_documents WHERE bill_id = ? ORDER BY id DESC LIMIT 1",
        [bill_id],
    )
    return rows[0]["content_hash"] if rows else None


def get_cached_chunks(bill_id: int) -> list[dict]:
    """Повертає існуючі чанки з rag_chunks або []."""
    rows = d1_query(
        "SELECT chunk_index, chunk_text, section FROM rag_chunks WHERE bill_id = ? ORDER BY chunk_index",
        [bill_id],
    )
    return [
        {"chunk_index": r["chunk_index"], "text": r["chunk_text"], "section": r["section"]}
        for r in rows
    ]


def save_hash(bill_id: int, content_hash: str) -> None:
    """Зберігає content_hash в rag_documents."""
    existing = d1_query(
        "SELECT id FROM rag_documents WHERE bill_id = ? LIMIT 1", [bill_id]
    )
    if existing:
        d1_exec("raw_sql", {
            "sql": "UPDATE rag_documents SET content_hash = ? WHERE bill_id = ?",
            "params": [content_hash, bill_id],
        })
    else:
        d1_exec("raw_sql", {
            "sql": "INSERT INTO rag_documents (bill_id, doc_type, title, content_hash) VALUES (?, ?, ?, ?)",
            "params": [bill_id, "cached", "cached", content_hash],
        })


def delete_existing_chunks(bill_id: int) -> None:
    """Видаляє старі чанки та документи для bill_id."""
    d1_exec("raw_sql", {
        "sql": "DELETE FROM rag_chunks WHERE bill_id = ?",
        "params": [bill_id],
    })
    d1_exec("raw_sql", {
        "sql": "DELETE FROM rag_documents WHERE bill_id = ?",
        "params": [bill_id],
    })


def insert_new_document(bill_id: int, bill_number: str, content_hash: str) -> int:
    """Вставляє новий запис rag_documents, повертає його id."""
    d1_exec("raw_sql", {
        "sql": "INSERT INTO rag_documents (bill_id, doc_type, title, content_hash) VALUES (?, ?, ?, ?)",
        "params": [bill_id, "combined", f"Закон #{bill_number}", content_hash],
    })
    rows = d1_query(
        "SELECT id FROM rag_documents WHERE bill_id = ? AND content_hash = ? ORDER BY id DESC LIMIT 1",
        [bill_id, content_hash],
    )
    return rows[0]["id"] if rows else 0


def insert_chunks(doc_id: int, bill_id: int, chunks: list[dict]) -> None:
    """Вставляє чанки тексту в rag_chunks."""
    for chunk in chunks:
        d1_exec("raw_sql", {
            "sql": "INSERT INTO rag_chunks (document_id, bill_id, chunk_index, chunk_text, section) VALUES (?, ?, ?, ?, ?)",
            "params": [doc_id, chunk["bill_id"], chunk["chunk_index"], chunk["text"][:2000], chunk["section"]],
        })


def find_bills_needing_rag(limit: int = 20) -> list[dict]:
    """Знаходить законопроекти, що потребують аналізу (нові, зі зміною статусу, або без LLM-аналізу)."""
    # Спочатку шукаємо в change_log (непозначені зміни)
    rows = d1_query(
        """
        SELECT DISTINCT b.id, b.bill_number, b.title, b.current_status,
               b.registration_date, b.committee, b.agenda_category,
               cl.change_type, cl.old_value, cl.new_value, b.url
        FROM change_log cl
        JOIN bills b ON cl.bill_id = b.id
        WHERE cl.notified = 0
          AND cl.change_type IN ('new', 'status_change')
        ORDER BY b.registration_date DESC
        LIMIT ?
        """,
        [limit],
    )

    result = [
        {
            "id": r["id"],
            "bill_number": r["bill_number"],
            "title": r["title"],
            "status": r["current_status"],
            "reg_date": r["registration_date"],
            "committee": r["committee"],
            "category": r["agenda_category"],
            "change_type": r["change_type"],
            "old_value": r["old_value"],
            "new_value": r["new_value"],
            "url": r["url"],
        }
        for r in rows
    ]

    if len(result) >= limit:
        return result

    # Якщо change_log порожній — шукаємо закони без LLM-аналізу
    remaining = limit - len(result)
    existing_ids = {r["id"] for r in result}

    unanalyzed = d1_query(
        """
        SELECT b.id, b.bill_number, b.title, b.current_status,
               b.registration_date, b.committee, b.agenda_category, b.url
        FROM bills b
        WHERE NOT EXISTS (
            SELECT 1 FROM risk_assessments r WHERE r.bill_id = b.id
        )
        ORDER BY b.registration_date DESC
        LIMIT ?
        """,
        [remaining],
    )

    for r in unanalyzed:
        if r["id"] not in existing_ids:
            result.append({
                "id": r["id"],
                "bill_number": r["bill_number"],
                "title": r["title"],
                "status": r["current_status"],
                "reg_date": r["registration_date"],
                "committee": r["committee"],
                "category": r["agenda_category"],
                "change_type": "new",
                "old_value": None,
                "new_value": None,
                "url": r["url"],
            })

    return result


def save_risk(document_id: int, data: dict, model: str) -> None:
    """Зберігає результати LLM-аналізу в risk_assessments (Chain of Thought формат)."""
    rows = d1_query("SELECT bill_id FROM rag_documents WHERE id = ?", [document_id])
    if not rows:
        raise RuntimeError(f"Missing rag_documents row for document_id={document_id}")
    bill_id = rows[0]["bill_id"]

    summary = data.get("summary", "")
    law_summary = data.get("law_summary", "")
    has_risks = data.get("has_risks", False)
    risk_level = data.get("risk_level", "low")
    detailed_risks = data.get("detailed_risks", [])
    analyzed_chunks = data.get("analyzed_chunks", [])

    risk_level_score = {"high": 3, "medium": 2, "low": 1}.get(risk_level, 1)
    overall_score = risk_level_score * 33.33 if has_risks else 0.0

    insufficient = bool(data.get("insufficient_text", False))
    confidence = 5 if insufficient else (1 if has_risks else 3)

    json_data = json.dumps(data, ensure_ascii=False)

    d1_exec("risk", {
        "document_id": document_id,
        "bill_id": bill_id,
        "model_used": model,
        "overall_score": float(overall_score),
        "budget_risk": json.dumps(detailed_risks, ensure_ascii=False),
        "legal_risk": json.dumps(detailed_risks, ensure_ascii=False),
        "economic_risk": json.dumps(detailed_risks, ensure_ascii=False),
        "social_risk": json.dumps(detailed_risks, ensure_ascii=False),
        "corruption_risk": json.dumps(detailed_risks, ensure_ascii=False),
        "raw_response": json_data,
        "raw_analysis": law_summary or summary,
        "json_data": json_data,
        "legislative_risk": json.dumps(analyzed_chunks, ensure_ascii=False),
        "official_power_risk": json.dumps([], ensure_ascii=False),
        "vague_norms_risk": json.dumps([], ensure_ascii=False),
        "confidence_level": confidence,
        "insufficient_text": insufficient,
    })

    log.info("RISK_SAVED: doc_id=%d bill_id=%d has_risks=%s risk_level=%s confidence=%d",
             document_id, bill_id, has_risks, risk_level, confidence)


def mark_notified(bill_ids: list[int]) -> None:
    """Позначає change_log як notified для списку bill_id."""
    if not bill_ids:
        return
    for bid in bill_ids:
        d1_exec("raw_sql", {
            "sql": "UPDATE change_log SET notified = 1 WHERE bill_id = ?",
            "params": [bid],
        })
    log.debug("Marked %d bills as notified", len(bill_ids))


def get_bill_documents(bill_id: int, bill_number: str | None = None) -> list[dict]:
    """Отримує документи законопроекту — спочатку з D1, потім з RADA API."""
    rows = d1_query(
        "SELECT file_id, doc_type FROM bill_documents WHERE bill_id = ?",
        [bill_id],
    )
    if rows:
        return [
            {"file_id": str(r["file_id"]), "kind": "source", "type": r["doc_type"] or "?", "name": ""}
            for r in rows
        ]

    if not bill_number:
        return []

    # Падаємо на RADA API
    import urllib.request
    url = "https://data.rada.gov.ua/ogd/zpr/skl9/billinfo_list-skl9.json"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    bills = json.loads(raw)
    for b in bills:
        if str(b.get("registrationNumber", "")) == str(bill_number):
            docs = b.get("documents", {})
            result = []
            for kind in ["source", "workflow"]:
                for d in docs.get(kind, []) or []:
                    dtype = d.get("kind", "?")
                    for f in d.get("docFiles", []) or []:
                        result.append({
                            "file_id": str(f["id"]),
                            "kind": kind,
                            "type": dtype,
                            "name": f.get("name", ""),
                        })
            return result
    return []
