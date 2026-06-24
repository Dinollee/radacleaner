"""d1_client.py — PostgreSQL client (replaces D1 Worker API).

All reads and writes go to PostgreSQL.
No more HTTP overhead, no D1 row write charges.

Usage:
    from src.d1_client import d1_query, d1_exec

    # SELECT
    rows = d1_query("SELECT * FROM bills WHERE stage = ?", [1])

    # INSERT/UPDATE via typed exec
    d1_exec("bill", {"bill_number": "1234", "title": "..."})

    # Raw SQL
    d1_exec("raw_sql", {"sql": "UPDATE bills SET x=%s", "params": [1]})
"""
import json
import os
import threading

import psycopg2
import psycopg2.extras

from .config import log

PG_DSN = os.environ.get(
    "PG_DSN",
    "host=192.168.1.244 dbname=radacleaner user=postgres password=164352"
)

_local = threading.local()


def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None or _local.conn.closed:
        _local.conn = psycopg2.connect(PG_DSN)
        _local.conn.autocommit = False
    return _local.conn


def _convert_params(sql: str, params: list | None) -> tuple:
    """Convert SQLite-style ? placeholders to PostgreSQL %s."""
    if not params:
        return sql, []
    return sql.replace("?", "%s"), list(params)


def d1_query(sql: str, params: list | None = None) -> list[dict]:
    """Execute SELECT query on PostgreSQL."""
    conn = _get_conn()
    sql, params = _convert_params(sql, params)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        log.error("d1_query error: %s | sql: %s", str(e)[:200], sql[:100])
        return []


def d1_exec(type_name: str, data: dict) -> bool:
    """Execute INSERT/UPDATE via typed operation."""
    conn = _get_conn()
    try:
        if type_name == "raw_sql":
            sql = data.get("sql", "")
            params = data.get("params", [])
            sql, params = _convert_params(sql, params)
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
            return True

        if type_name == "bill":
            return _exec_bill(conn, data)
        if type_name == "risk":
            return _exec_risk(conn, data)
        if type_name == "change_log":
            return _exec_change_log(conn, data)
        if type_name == "law_version":
            return _exec_law_version(conn, data)
        if type_name == "eu_alignment":
            return _exec_eu_alignment(conn, data)
        if type_name == "refresh_stats":
            return _exec_refresh_stats(conn)

        log.warning("d1_exec unknown type: %s", type_name)
        return False

    except Exception as e:
        log.error("d1_exec %s error: %s", type_name, str(e)[:200])
        conn.rollback()
        return False


def _exec_bill(conn, data: dict) -> bool:
    bn = data.get("bill_number", "")
    if not bn:
        return False

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id FROM bills WHERE bill_number=%s", (bn,))
        existing = cur.fetchone()

        if existing:
            sets, vals = [], []
            for k in ("current_status", "title", "registration_date", "committee",
                       "agenda_category", "url", "stage", "act_number", "act_date",
                       "status_changed_at", "is_procedural", "last_card_check", "card_hash"):
                if k in data and data[k] is not None:
                    sets.append(f"{k}=%s")
                    vals.append(data[k])
            if sets:
                sets.append("updated_at=now() AT TIME ZONE 'utc'")
                vals.append(bn)
                cur.execute(f"UPDATE bills SET {','.join(sets)} WHERE bill_number=%s", vals)
        else:
            cols = ["bill_number", "title", "current_status", "registration_date",
                    "committee", "agenda_category", "url", "stage", "act_number", "act_date"]
            present = [c for c in cols if c in data]
            placeholders = ",".join(["%s"] * len(present))
            col_names = ",".join(present)
            vals = [data[c] for c in present]
            cur.execute(f"INSERT INTO bills ({col_names}) VALUES ({placeholders})", vals)

    conn.commit()
    return True


def _exec_risk(conn, data: dict) -> bool:
    bill_id = data.get("bill_id")
    if not bill_id and data.get("bill_number"):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM bills WHERE bill_number=%s", (data["bill_number"],))
            row = cur.fetchone()
            bill_id = row[0] if row else None
    if not bill_id:
        return False

    cols = ["document_id", "bill_id", "model_used", "overall_score",
            "budget_risk", "legal_risk", "economic_risk", "social_risk",
            "corruption_risk", "raw_response", "raw_analysis", "json_data",
            "legislative_risk", "official_power_risk", "vague_norms_risk",
            "confidence_level", "insufficient_text",
            "significance", "impact", "risk_score", "toxicity", "risk_level",
            "urgency", "time_context", "stakeholders", "risk_signals"]
    present = [c for c in cols if c in data]
    placeholders = ",".join(["%s"] * len(present))
    col_names = ",".join(present)
    vals = [data[c] for c in present]
    # Convert boolean to int for PG integer columns
    for i, c in enumerate(present):
        if c in ("insufficient_text",) and isinstance(vals[i], bool):
            vals[i] = 1 if vals[i] else 0

    with conn.cursor() as cur:
        cur.execute(f"SELECT bill_id FROM risk_assessments WHERE bill_id=%s", (bill_id,))
        existing = cur.fetchone()
        if existing:
            update_parts = [f"{c}=excluded.{c}" for c in present]
            cur.execute(
                f"INSERT INTO risk_assessments ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT(bill_id) DO UPDATE SET {','.join(update_parts)}",
                vals
            )
        else:
            cur.execute(f"INSERT INTO risk_assessments ({col_names}) VALUES ({placeholders})", vals)

        try:
            parsed = json.loads(data.get("json_data", "{}") or "{}")
            if "is_procedural" in parsed:
                cur.execute("UPDATE bills SET is_procedural=%s WHERE id=%s",
                           (1 if parsed["is_procedural"] else 0, bill_id))
            if parsed.get("risk_level"):
                cur.execute("UPDATE risk_assessments SET risk_level=%s WHERE bill_id=%s",
                           (parsed["risk_level"], bill_id))
        except Exception:
            pass

    conn.commit()
    return True


def _exec_change_log(conn, data: dict) -> bool:
    bill_id = data.get("bill_id")
    if not bill_id and data.get("bill_number"):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM bills WHERE bill_number=%s", (data["bill_number"],))
            row = cur.fetchone()
            bill_id = row[0] if row else None
    if not bill_id:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO change_log (bill_id, change_type, old_value, new_value) VALUES (%s,%s,%s,%s)",
            (bill_id, data.get("change_type", ""), data.get("old_value"), data.get("new_value"))
        )
    conn.commit()
    return True


def _exec_law_version(conn, data: dict) -> bool:
    law_id = data.get("law_id")
    if not law_id and data.get("bill_number"):
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM bills WHERE bill_number=%s", (data["bill_number"],))
            row = cur.fetchone()
            law_id = row[0] if row else None
    if not law_id:
        return False
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO law_versions (law_id, status_at_moment, text_hash, plain_text, analysis_summary, risks_json) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(law_id, text_hash) DO NOTHING",
            (law_id, data.get("status_at_moment", ""), data.get("text_hash", ""),
             data.get("plain_text", ""), data.get("analysis_summary", ""), data.get("risks_json", "{}"))
        )
    conn.commit()
    return True


def _exec_eu_alignment(conn, data: dict) -> bool:
    eu_type = data.get("type")
    with conn.cursor() as cur:
        if eu_type == "overall":
            cur.execute(
                """INSERT INTO eu_alignment_overall 
                   (overall_score, weighted_score, chapters_analyzed, total_chapters, calculated_at,
                    signed_score, in_process_score, signed_bills, in_process_bills) 
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (data.get("overall_score", 0), data.get("weighted_score", 0),
                 data.get("chapters_analyzed", 0), data.get("total_chapters", 35),
                 data.get("calculated_at", ""),
                 data.get("signed_score", 0), data.get("in_process_score", 0),
                 data.get("signed_bills", 0), data.get("in_process_bills", 0))
            )
        elif eu_type == "chapter":
            cur.execute(
                "INSERT INTO eu_alignment_chapters (chapter_id, chapter_name, chapter_name_en, alignment, total_bills, keywords_matched, total_keywords, weight, calculated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (data.get("chapter_id"), data.get("chapter_name", ""), data.get("chapter_name_en", ""),
                 data.get("alignment", 0), data.get("total_bills", 0),
                 data.get("keywords_matched", 0), data.get("total_keywords", 0),
                 data.get("weight", 1.0), data.get("calculated_at", ""))
            )
    conn.commit()
    return True


def _exec_refresh_stats(conn) -> bool:
    queries = {
        "total_bills": "SELECT COUNT(*) FROM bills",
        "analyzed_bills": "SELECT COUNT(DISTINCT bill_id) FROM risk_assessments",
        "high_risk": "SELECT COUNT(*) FROM risk_assessments WHERE risk_level='high' OR overall_score>=70",
        "medium_risk": "SELECT COUNT(*) FROM risk_assessments WHERE risk_level='medium' OR (overall_score>=40 AND overall_score<70)",
        "procedural_bills": "SELECT COUNT(*) FROM bills WHERE is_procedural=1 OR (is_procedural IS NULL AND agenda_category IN ('Організаційні питання','Інші (заяви, звернення ВРУ)'))",
        "total_votes": "SELECT COUNT(*) FROM votes",
        "total_mps": "SELECT COUNT(*) FROM mps",
        "active_mps": "SELECT COUNT(*) FROM mps WHERE end_date IS NULL OR end_date=''",
        "new_bills_24h": "SELECT COUNT(*) FROM bills WHERE registration_date>=to_char(CURRENT_DATE - INTERVAL '1 day', 'YYYY-MM-DD')",
        "status_changes_24h": "SELECT COUNT(*) FROM change_log WHERE change_type='status_change' AND created_at>=to_char((now() AT TIME ZONE 'utc') - INTERVAL '1 day', 'YYYY-MM-DD HH24:MI:SS')",
        "recent_changes": "SELECT COUNT(*) FROM change_log WHERE created_at>to_char((now() AT TIME ZONE 'utc') - INTERVAL '7 days', 'YYYY-MM-DD HH24:MI:SS')",
        "avg_toxicity": "SELECT COALESCE(AVG(toxicity), 0) FROM bills WHERE toxicity IS NOT NULL",
    }

    with conn.cursor() as cur:
        for key, sql in queries.items():
            cur.execute(sql)
            val = str(cur.fetchone()[0])
            cur.execute(
                "INSERT INTO stats_cache (key, value, updated_at) VALUES (%s,%s,(now() AT TIME ZONE 'utc')) "
                "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at",
                (key, val)
            )

        cur.execute(
            "SELECT stage, COUNT(*) as count FROM bills WHERE stage IS NOT NULL GROUP BY stage ORDER BY stage"
        )
        by_stage = [{"stage": r[0], "count": r[1]} for r in cur.fetchall()]
        cur.execute(
            "INSERT INTO stats_cache (key, value, updated_at) VALUES (%s,%s,(now() AT TIME ZONE 'utc')) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at",
            ("by_stage", json.dumps(by_stage))
        )

    conn.commit()
    log.info("Stats cache refreshed (PostgreSQL)")
    return True


def d1_exec_sql(sql: str, params: list | None = None) -> bool:
    """Execute raw SQL on PostgreSQL."""
    conn = _get_conn()
    sql, params = _convert_params(sql, params)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        log.error("d1_exec_sql error: %s", str(e)[:200])
        conn.rollback()
        return False


def refresh_stats_cache() -> bool:
    """Refresh dashboard statistics cache in PostgreSQL."""
    return d1_exec("refresh_stats", {})
