"""Minimal batch RAG for VRU bills using Groq only."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

import psycopg2
import requests


def get_groq_key() -> str:
    env = os.environ.get("GROQ_API_KEY", "") or os.environ.get("GROQ_TOKEN", "")
    if env:
        return env.strip()
    cfg = "/root/.hermes/config.yaml"
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            for line in f:
                if "gsk_" in line and ("api_key" in line or "groq" in line):
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if val.startswith("gsk_"):
                        return val
    except Exception:
        pass
    return ""


GROQ_API_KEY = get_groq_key()
GROQ_API_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "openai/gpt-oss-120b"

DB_PARAMS = {
    "host": os.environ.get("DB_HOST", "192.168.1.229"),
    "dbname": os.environ.get("DB_NAME", "my_bills"),
    "user": os.environ.get("DB_USER", "hermes"),
    "password": os.environ.get("DB_PASSWORD", "hermes"),
}

parser = argparse.ArgumentParser()
parser.add_argument("--limit", type=int, default=50)
args = parser.parse_args()
LIMIT = args.limit


def get_conn():
    return psycopg2.connect(**DB_PARAMS)


def next_batch(limit: int):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT d.id, d.bill_id, d.file_id, d.title
            FROM rag_documents d
            WHERE NOT EXISTS (
                SELECT 1 FROM risk_assessments r
                WHERE r.document_id = d.id
            )
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def fetch_text(document_id: int) -> str:
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT chunk_text FROM rag_chunks WHERE document_id = %s ORDER BY chunk_index",
            (document_id,),
        )
        rows = [r[0] for r in cur.fetchall() if r[0]]

    uniq, seen = [], set()
    for text in rows:
        short = text[:120]
        if short not in seen:
            seen.add(short)
            uniq.append(text)
    return "\n".join(uniq)


def rag_answer(prompt: str) -> dict:
    headers = {
        "Authorization": "Bearer " + GROQ_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Твоя задача — оцінити ризики законопроєкту. Відповідай українською, лише JSON без додаткових коментарів.",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1600,
    }
    last_exc = None
    for attempt in range(1, 6):
        try:
            with requests.post(
                GROQ_API_URL + "/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,
            ) as resp:
                if resp.status_code == 429:
                    wait = min(2 ** attempt, 60)
                    try:
                        wait = max(wait, int(resp.headers.get("retry-after", "0")))
                    except Exception:
                        pass
                    last_exc = RuntimeError(f"rate limited {attempt}/5 wait={wait}s")
                    print(f" LLM_RETRY {attempt}/5 wait={wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                text = resp.json()["choices"][0]["message"]["content"]
                print("LLM_RAW:", text[:1200])
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    m = re.search(r"\{[\s\S]+\}", text)
                    return json.loads(m.group(0)) if m else {}
        except Exception as e:
            last_exc = e
            print(f" LLM_ERR {type(e).__name__}: {str(e)[:200]}")
            time.sleep(2 ** attempt)
    raise last_exc or RuntimeError("LLM failed")


def save_risk(document_id: int, data: dict, model: str):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT bill_id FROM rag_documents WHERE id = %s", (document_id,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("missing rag_documents row for document_id=" + str(document_id))
        bill_id = row[0]
        budget = data.get("legislative_risk", {"finding": "Не виявлено"})
        legal = data.get("official_power_risk", {"finding": "Не виявлено"})
        economic = data.get("vague_norms_risk", {"finding": "Не виявлено"})
        social = data.get("economic_effect", "Нейтральний")
        corruption = data.get("criticality", {"level": "Низька", "justification": ""})
        overall = float(data.get("confidence_level", 5.0)) if data.get("confidence_level") else 5.0
        confidence = int(data.get("confidence_level", 5))
        insufficient = bool(data.get("insufficient_text", False))
        cur.execute(
            """
            INSERT INTO risk_assessments
                (document_id, bill_id, assessed_at, model_used, budget_risk, legal_risk, economic_risk, social_risk, corruption_risk, overall_score, raw_response, raw_analysis, json_data, legislative_risk, official_power_risk, vague_norms_risk, confidence_level, insufficient_text)
            VALUES (%s, %s, now(), %s, %s, %s, %s, %s, %s, %s::numeric, %s, %s, %s, %s, %s, %s, %s, %s)
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
                json.dumps(budget, ensure_ascii=False),
                json.dumps(legal, ensure_ascii=False),
                json.dumps(economic, ensure_ascii=False),
                social,
                json.dumps(corruption, ensure_ascii=False),
                overall,
                json.dumps(data, ensure_ascii=False),
                data.get("summary", ""),
                json.dumps(data, ensure_ascii=False),
                json.dumps(budget, ensure_ascii=False),
                json.dumps(legal, ensure_ascii=False),
                json.dumps(economic, ensure_ascii=False),
                confidence,
                insufficient,
            ),
        )
        conn.commit()
    print("RISK_SAVED:", document_id, "overall=", overall)


def main():
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY missing")
        sys.exit(2)
    print("START limit=%s groq=%s" % (LIMIT, bool(GROQ_API_KEY)))
    rows = next_batch(LIMIT)
    print("BATCH:", len(rows))
    done = 0
    skip = 0
    for idx, (id_, bill_id, file_id, title) in enumerate(rows, start=1):
        print(f"[{idx}/{len(rows)}] doc_id={id_} bill_id={bill_id} title={title!r}")
        text = fetch_text(id_)
        if not text:
            print(" SKIP: empty text")
            skip += 1
            if idx < len(rows):
                time.sleep(1)
            continue
        print(" TEXT_LEN:", len(text))
        substantive = [t for t in text.split('\n\n') if any(w in t for w in ['стаття', 'Угода', 'Позик', 'Меморандум', 'фінансов', 'Кредитор', 'Позичальник', 'макрофінансова'])]
        ctx = '\n\n'.join(substantive[:5]) if substantive else text[:4000]
        insufficient = len(ctx.strip()) < 1200
        prompt = (
            "Ти — незалежний експерт з українського законодавства. Проаналізуй текст законопроєкту.\n"
            "Відповідай українською, ТІЛЬКИ JSON, без додаткового тексту.\n"
            "Критичні маркери: звуження прав громадян, розширення повноважень чиновників, корупційні/розмиті норми, фінансово-економічний вплив.\n"
            '{\n'
            ' "summary": " Суть змін: 1-2 речення без пророцтв.",\n'
            ' "legislative_risk": {"finding":"Факти або Не виявлено"},\n'
            ' "official_power_risk": {"finding":"Факти або Не виявлено"},\n'
            ' "vague_norms_risk": {"finding":"Конкретні цитати/розділи або Не виявлено"},\n'
            ' "economic_effect": "Вплив на бюджет/бізнес або Нейтральний",\n'
            ' "criticality": {"level":"Низька/Середня/Висока","justification":"Коротке обґрунтування"},\n'
            ' "confidence_level": 1-5,\n'
            ' "insufficient_text": true/false\n'
            '}\n'
            "Правила:\n"
            "- Не вигадуй наслідки, аналізуй лише наведений текст.\n"
            "- Не давай оцінок емоційного характеру, без політичних лозунгів.\n"
            "- При недоступності інформації — пиши \"Не виявлено\".\n"
            "- confidence_level: 1 = текст повний, 5 = текст обмежений.\n\n"
            "Текст:\n" + ctx
        )
        try:
            data = rag_answer(prompt)
        except Exception as e:
            print(" LLM_FAIL:", type(e).__name__, str(e)[:200])
            if idx < len(rows):
                time.sleep(2.5)
            continue
        try:
            save_risk(id_, data, GROQ_MODEL)
            done += 1
        except Exception as e:
            print(" SAVE_FAIL:", type(e).__name__, str(e)[:200])
        if idx < len(rows):
            time.sleep(2.5)
    print("DONE done=%d skip=%d" % (done, skip))


if __name__ == "__main__":
    main()
