"""Збереження та отримання оцінок ризиків з БД."""
import json
import logging

import psycopg2

from .config import DB_PARAMS

log = logging.getLogger(__name__)

# Мапа severity → вага (для розрахунку overall_score)
SEVERITY_WEIGHTS = {"High": 3, "Medium": 2, "Low": 1}


def db_conn():
    """Повертає нове з'єднання з БД."""
    return psycopg2.connect(**DB_PARAMS)


def get_stored_hash(bill_id: int) -> str | None:
    """Повертає content_hash з rag_documents для bill_id або None."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM rag_documents WHERE bill_id=%s ORDER BY id DESC LIMIT 1",
            (bill_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_cached_chunks(bill_id: int) -> list[dict]:
    """Повертає існуючі чанки з rag_chunks або []."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_index, chunk_text, section FROM rag_chunks WHERE bill_id=%s ORDER BY chunk_index",
            (bill_id,),
        )
        return [
            {"chunk_index": r[0], "text": r[1], "section": r[2]}
            for r in cur.fetchall()
        ]


def save_hash(bill_id: int, content_hash: str) -> None:
    """Зберігає content_hash в rag_documents."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE rag_documents SET content_hash=%s WHERE bill_id=%s",
            (content_hash, bill_id),
        )
        if cur.rowcount == 0:
            cur.execute(
                "INSERT INTO rag_documents (bill_id, doc_type, title, content_hash) VALUES (%s,'cached','cached',%s)",
                (bill_id, content_hash),
            )
        conn.commit()


def delete_existing_chunks(bill_id: int) -> None:
    """Видаляє старі чанки та документи для bill_id."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rag_chunks WHERE bill_id = %s", (bill_id,))
        cur.execute("DELETE FROM rag_documents WHERE bill_id = %s", (bill_id,))


def insert_new_document(bill_id: int, bill_number: str, content_hash: str) -> int:
    """Вставляє новий запис rag_documents, повертає його id."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO rag_documents (bill_id, doc_type, title, content_hash) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (bill_id, "combined", f"Закон #{bill_number}", content_hash),
        )
        doc_id = cur.fetchone()[0]
        conn.commit()
        return doc_id


def insert_chunks(doc_id: int, bill_id: int, chunks: list[dict]) -> None:
    """Вставляє чанки тексту в rag_chunks."""
    with db_conn() as conn, conn.cursor() as cur:
        for chunk in chunks:
            cur.execute(
                "INSERT INTO rag_chunks (document_id, bill_id, chunk_index, chunk_text, section) "
                "VALUES (%s, %s, %s, %s, %s)",
                (
                    doc_id,
                    chunk["bill_id"],
                    chunk["chunk_index"],
                    chunk["text"][:2000],
                    chunk["section"],
                ),
            )
        conn.commit()


def find_bills_needing_rag(limit: int = 20) -> list[dict]:
    """Знаходить законопроекти, що потребують аналізу (нові або зі зміною статусу)."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT b.id, b.bill_number, b.title, b.current_status,
                   b.registration_date, b.committee, b.agenda_category,
                   cl.change_type, cl.old_value, cl.new_value, b.url
            FROM change_log cl
            JOIN bills b ON cl.bill_id = b.id
            WHERE cl.notified = false
              AND cl.change_type IN ('new', 'status_change')
            ORDER BY b.registration_date DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    return [
        {
            "id": r[0],
            "bill_number": r[1],
            "title": r[2],
            "status": r[3],
            "reg_date": r[4],
            "committee": r[5],
            "category": r[6],
            "change_type": r[7],
            "old_value": r[8],
            "new_value": r[9],
            "url": r[10],
        }
        for r in rows
    ]


def save_risk(document_id: int, data: dict, model: str) -> None:
    """Зберігає результати LLM-аналізу в risk_assessments."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT bill_id FROM rag_documents WHERE id = %s", (document_id,)
        )
        row = cur.fetchone()
        if not row:
            raise RuntimeError(
                f"Missing rag_documents row for document_id={document_id}"
            )
        bill_id = row[0]

        # Новий формат: risks[] з category, severity, quote, explanation
        risks = data.get("risks", [])
        summary = data.get("summary", "")

        risks_json = json.dumps(risks, ensure_ascii=False)
        legislative_risk = {
            "summary": summary,
            "risks": [r for r in risks if r.get("category") == "Civil Rights"],
        }
        official_power_risk = {
            "risks": [r for r in risks if r.get("category") == "Power Concentration"]
        }
        vague_norms_risk = {
            "risks": [
                r
                for r in risks
                if r.get("category") in ("Ambiguity", "Legal Collision")
            ]
        }
        economic_risk = {
            "risks": [r for r in risks if r.get("category") == "Budgetary"]
        }
        corruption_risk = {
            "risks": [r for r in risks if r.get("category") == "Corruption"]
        }

        # Overall score: High=3, Medium=2, Low=1 → середнє
        if risks:
            overall_score = (
                sum(
                    SEVERITY_WEIGHTS.get(r.get("severity", "Low"), 1)
                    for r in risks
                )
                / len(risks)
                * 33.33
            )
        else:
            overall_score = 0.0

        insufficient = bool(data.get("insufficient_text", False))

        # confidence_level: чим більше ризиків — тим нижче (більше тексту проаналізовано)
        confidence = 5 if insufficient else min(5, max(1, 6 - len(risks)))

        cur.execute(
            """
            INSERT INTO risk_assessments
                (document_id, bill_id, assessed_at, model_used,
                 budget_risk, legal_risk, economic_risk, social_risk, corruption_risk,
                 overall_score, raw_response, raw_analysis, json_data,
                 legislative_risk, official_power_risk, vague_norms_risk,
                 confidence_level, insufficient_text)
            VALUES (%s, %s, now(), %s,
                    %s, %s, %s, %s, %s,
                    %s::numeric, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s)
            ON CONFLICT (bill_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                assessed_at = now(),
                model_used = EXCLUDED.model_used,
                budget_risk = EXCLUDED.budget_risk,
                legal_risk = EXCLUDED.legal_risk,
                economic_risk = EXCLUDED.economic_risk,
                social_risk = EXCLUDED.social_risk,
                corruption_risk = EXCLUDED.corruption_risk,
                overall_score = EXCLUDED.overall_score,
                raw_response = EXCLUDED.raw_response,
                raw_analysis = EXCLUDED.raw_analysis,
                json_data = EXCLUDED.json_data,
                legislative_risk = EXCLUDED.legislative_risk,
                official_power_risk = EXCLUDED.official_power_risk,
                vague_norms_risk = EXCLUDED.vague_norms_risk,
                confidence_level = EXCLUDED.confidence_level,
                insufficient_text = EXCLUDED.insufficient_text
            """,
            (
                document_id,
                bill_id,
                model,
                json.dumps(legislative_risk, ensure_ascii=False),
                json.dumps(official_power_risk, ensure_ascii=False),
                json.dumps(vague_norms_risk, ensure_ascii=False),
                json.dumps(economic_risk, ensure_ascii=False),
                json.dumps(corruption_risk, ensure_ascii=False),
                float(overall_score),
                json.dumps(data, ensure_ascii=False),
                summary,
                risks_json,
                json.dumps(legislative_risk, ensure_ascii=False),
                json.dumps(official_power_risk, ensure_ascii=False),
                json.dumps(vague_norms_risk, ensure_ascii=False),
                confidence,
                insufficient,
            ),
        )
        conn.commit()
        log.info("RISK_SAVED: doc_id=%d bill_id=%d confidence=%d", document_id, bill_id, confidence)


def mark_notified(bill_ids: list[int]) -> None:
    """Позначає change_log як notified для списку bill_id."""
    if not bill_ids:
        return
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE change_log SET notified = true WHERE bill_id = ANY(%s)",
            (bill_ids,),
        )
        conn.commit()
    log.debug("Marked %d bills as notified", len(bill_ids))


def get_bill_documents(bill_id: int, bill_number: str | None = None) -> list[dict]:
    """Отримує документи законопроекту — спочатку з БД, потім з RADA API."""
    with db_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT file_id, doc_type FROM bill_documents WHERE bill_id = %s",
            (bill_id,),
        )
        rows = cur.fetchall()
        if rows:
            return [
                {"file_id": str(r[0]), "kind": "source", "type": r[1] or "?", "name": ""}
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
                        result.append(
                            {
                                "file_id": str(f["id"]),
                                "kind": kind,
                                "type": dtype,
                                "name": f.get("name", ""),
                            }
                        )
            return result
    return []