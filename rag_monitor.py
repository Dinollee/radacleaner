#!/usr/bin/env python3
"""rag_monitor.py — Groq + новий аналітичний формат. Без CF embeddings."""
import sys, os, json, re, time, urllib.request, asyncio
from datetime import datetime

sys.path.insert(0, "/home/test-agent/bot")

import psycopg2
import requests
import hashlib

DB = dict(host="192.168.1.229", port=5432, dbname="my_bills", user="hermes", password="hermes")

def get_groq_key() -> str:
    env = os.environ.get("GROQ_API_KEY", "") or os.environ.get("GROQ_TOKEN", "")
    if env:
        return env.strip()
    env_file = "/home/test-agent/bot/.env"
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GROQ_API_KEY") or line.startswith("GROQ_TOKEN"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    except Exception:
        pass
    return ""

GROQ_API_KEY = get_groq_key()
GROQ_API_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "openai/gpt-oss-120b"

def db():
    return psycopg2.connect(**DB)

def get_rada_token():
    req = urllib.request.Request("https://data.rada.gov.ua/api/token")
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())['token']

def get_stored_hash(bill_id):
    """Повертає content_hash з rag_documents для bill_id або None."""
    c = db(); cur = c.cursor()
    cur.execute("SELECT content_hash FROM rag_documents WHERE bill_id=%s ORDER BY id DESC LIMIT 1", (bill_id,))
    row = cur.fetchone()
    cur.close(); c.close()
    return row[0] if row else None

def get_cached_chunks(bill_id):
    """Повертає існуючі чанки з rag_chunks або []."""
    c = db(); cur = c.cursor()
    cur.execute("SELECT chunk_index, chunk_text, section FROM rag_chunks WHERE bill_id=%s ORDER BY chunk_index", (bill_id,))
    rows = cur.fetchall()
    cur.close(); c.close()
    return [{'chunk_index': r[0], 'text': r[1], 'section': r[2]} for r in rows]

def save_hash(bill_id, content_hash):
    """Зберігає content_hash в rag_documents."""
    c = db(); cur = c.cursor()
    cur.execute("UPDATE rag_documents SET content_hash=%s WHERE bill_id=%s", (content_hash, bill_id))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO rag_documents (bill_id, doc_type, title, content_hash) VALUES (%s,'cached','cached',%s)", (bill_id, content_hash))
    c.commit(); cur.close(); c.close()
def download_rada_pdf(file_id, token):
    base = "https://itd.rada.gov.ua/billinfo/api/file/download/"
    all_data = []
    chunk = 0
    total_size = None
    while True:
        req = urllib.request.Request(base + f"?id={file_id}", headers={
            "User-Agent": token, "X-File-Id": str(file_id),
            "X-Current-Chunk": str(chunk),
            "Referer": f"https://itd.rada.gov.ua/billInfo/Bills/pubFile/{file_id}",
        })
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
            if chunk == 0:
                total_size = int(resp.headers.get('Size', '0'))
            all_data.append(data)
            if total_size and sum(len(d) for d in all_data) >= total_size:
                break
            if len(data) == 0:
                break
            chunk += 1
            if chunk > 200:
                break
    return b''.join(all_data)

def extract_pdf_text(path):
    import fitz
    doc = fitz.open(path)
    text = ''.join(page.get_text() for page in doc)
    doc.close()
    return text

def chunk_text(text, max_size=600):
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    paragraphs = [p.strip() for p in text.split('\n') if p.strip() and len(p.strip()) > 15]
    chunks = []
    current = ''
    for para in paragraphs:
        if len(current) + len(para) < max_size:
            current += '\n' + para if current else para
        else:
            if current:
                chunks.append(current.strip())
            current = para
    if current and len(current) > 30:
        chunks.append(current.strip())
    return chunks

def find_bills_needing_rag():
    c = db(); cur = c.cursor()
    cur.execute("""
        SELECT DISTINCT b.id, b.bill_number, b.title, b.current_status,
               b.registration_date, b.committee, b.agenda_category,
               cl.change_type, cl.old_value, cl.new_value, b.url
        FROM change_log cl
        JOIN bills b ON cl.bill_id = b.id
        WHERE cl.notified = false
          AND cl.change_type IN ('new', 'status_change')
        ORDER BY b.registration_date DESC
        LIMIT 20
    """)
    rows = cur.fetchall()
    cur.close(); c.close()
    return rows

def get_bill_documents(bill_id, bill_number=None):
    """Get document file_ids — first from local DB, then from RADA API"""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT file_id, doc_type FROM bill_documents WHERE bill_id = %s", (bill_id,))
        rows = cur.fetchall()
        if rows:
            return [{'file_id': str(r[0]), 'kind': 'source', 'type': r[1] or '?', 'name': ''} for r in rows]
    if not bill_number:
        return []
    url = "https://data.rada.gov.ua/ogd/zpr/skl9/billinfo_list-skl9.json"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    bills = json.loads(raw)
    for b in bills:
        if str(b.get("registrationNumber","")) == str(bill_number):
            docs = b.get("documents", {})
            result = []
            for kind in ['source', 'workflow']:
                for d in (docs.get(kind, []) or []):
                    dtype = d.get('kind', '?')
                    for f in (d.get('docFiles', []) or []):
                        result.append({'file_id': str(f['id']), 'kind': kind, 'type': dtype, 'name': f.get('name', '')})
            return result
    return []

def rag_answer(prompt: str) -> dict:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY missing")
    headers = {"Authorization": "Bearer " + GROQ_API_KEY, "Content-Type": "application/json"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": "Ти — незалежний експерт з українського законодавства. Відповідай українською, лише JSON без додаткових коментарів."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1600,
    }
    last_exc = None
    for attempt in range(1, 6):
        try:
            with requests.post(GROQ_API_URL + "/chat/completions", headers=headers, json=payload, timeout=120) as resp:
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
                print("LLM_RAW:\n", text[:2000])
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

def save_risk(document_id, data, model):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT bill_id FROM rag_documents WHERE id = %s", (document_id,))
        row = cur.fetchone()
        if not row:
            raise RuntimeError("missing rag_documents row for document_id=" + str(document_id))
        bill_id = row[0]

        # Новий формат: risks[] з category, severity, quote, explanation
        risks = data.get("risks", [])
        summary = data.get("summary", "")

        # Конвертуємо risks[] в поля для БД
        risks_json = json.dumps(risks, ensure_ascii=False)
        legislative_risk = {"summary": summary, "risks": [r for r in risks if r.get("category") == "Civil Rights"]}
        official_power_risk = {"risks": [r for r in risks if r.get("category") == "Power Concentration"]}
        vague_norms_risk = {"risks": [r for r in risks if r.get("category") in ("Ambiguity", "Legal Collision")]}
        economic_risk_val = {"risks": [r for r in risks if r.get("category") == "Budgetary"]}
        corruption_risk_val = {"risks": [r for r in risks if r.get("category") == "Corruption"]}

        # Overall score: High=3, Medium=2, Low=1 → середнє
        sev_map = {"High": 3, "Medium": 2, "Low": 1}
        if risks:
            overall_score = sum(sev_map.get(r.get("severity", "Low"), 1) for r in risks) / len(risks) * 33.33
        else:
            overall_score = 0

        insufficient = bool(data.get("insufficient_text", False))
        confidence = 5 if insufficient else min(5, max(1, 6 - len(risks)))
        cur.execute("""
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
        """, (
            document_id, bill_id, model,
            json.dumps(legislative_risk, ensure_ascii=False),
            json.dumps(official_power_risk, ensure_ascii=False),
            json.dumps(vague_norms_risk, ensure_ascii=False),
            json.dumps(economic_risk_val, ensure_ascii=False),
            json.dumps(corruption_risk_val, ensure_ascii=False),
            float(overall_score),
            json.dumps(data, ensure_ascii=False),
            summary,
            risks_json,
            json.dumps(legislative_risk, ensure_ascii=False),
            json.dumps(official_power_risk, ensure_ascii=False),
            json.dumps(vague_norms_risk, ensure_ascii=False),
            confidence,
            insufficient,
        ))
        conn.commit()
    print(f"RISK_SAVED: doc_id={document_id} bill_id={bill_id} confidence={confidence}")

def get_bill_versions(bill_id, limit=10):
    """Повертає список версій закону з історією змін."""
    c = db(); cur = c.cursor()
    cur.execute("""
        SELECT id, version_date, status_at_moment, text_hash,
               LEFT(plain_text, 200) as text_preview,
               analysis_summary, risks_json
        FROM law_versions
        WHERE law_id = %s
        ORDER BY version_date DESC
        LIMIT %s
    """, (bill_id, limit))
    rows = cur.fetchall()
    cur.close(); c.close()
    return [{
        'id': r[0], 'date': r[1], 'status': r[2], 'hash': r[3],
        'preview': r[4], 'summary': r[5], 'risks': r[6]
    } for r in rows]

def compare_versions(old_text, new_text):
    """Повертає diff між двома версіями тексту (рядки що додано/видалено)."""
    import difflib
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm='', fromfile='Попередня версія', tofile='Нова версія'))
    return '\n'.join(diff[:50])  # максимум 50 рядків diff

def format_tg_message(info, data):
    bill_id = info['id']
    bill_number = info['bill_number']
    title = info['title']
    status = info['status']
    bill_url = info.get('url', '')
    lines = []
    if bill_url:
        lines.append(f"📜 <b>#{bill_number}</b> — <a href='{bill_url}'>{title[:80]}</a>")
    else:
        lines.append(f"📜 <b>#{bill_number}</b> — {title[:80]}")
    # legislative progress bar
    status_map = {
        'new': (1, 'Зареєстровано'), 'У процесі': (2, 'У процесі'),
        'Перше читання': (3, 'Перше читання'), 'Друге читання': (4, 'Друге читання'),
        'Підписано': (5, 'Підписано'), 'Відхилено': (5, 'Відхилено'),
    }
    step, step_name = status_map.get(status, (1, status or 'Невідомо'))
    bar = '█' * step + '░' * (5 - step)
    reg_date = info.get("reg_date","")
    date_str = f" — зареєстровано {reg_date}" if reg_date else ""
    lines.append(f"📊 Прогрес: {bar} {step_name}{date_str}")
    lines.append(f"💡 Суть: {data.get('summary','—')[:150]}")

    # Ризики з нового формату risks[]
    risks = data.get('risks', [])
    if risks:
        # Сортуємо за severity: High → Medium → Low
        severity_order = {'High': 0, 'Medium': 1, 'Low': 2}
        risks_sorted = sorted(risks, key=lambda r: severity_order.get(r.get('severity',''), 9))

        # Емодзі за категорією
        cat_emoji = {
            'Corruption': '💰', 'Budgetary': '💵', 'Legal Collision': '⚖️',
            'Ambiguity': '⚠️', 'Civil Rights': '👤', 'Power Concentration': '🏛',
            'Other': '📌',
        }
        # Severity emoji
        sev_emoji = {'High': '🔴', 'Medium': '🟠', 'Low': '🟡'}

        for risk in risks_sorted[:5]:  # максимум 5 ризиків
            cat = risk.get('category', 'Other')
            sev = risk.get('severity', 'Low')
            emoji = cat_emoji.get(cat, '📌')
            sev_icon = sev_emoji.get(sev, '🟡')
            quote = risk.get('quote', '')[:100]
            explanation = risk.get('explanation', '')[:120]
            lines.append(f"{emoji} <b>{cat}</b> {sev_icon} {sev}")
            if quote:
                lines.append(f"   📝 «{quote}»")
            if explanation:
                lines.append(f"   💬 {explanation}")
            lines.append("")
    else:
        lines.append("✅ Ризиків не виявлено")

    # Якщо текст обмежений
    if data.get('insufficient_text'):
        lines.append("⚠️ <i>Текст обмежений — аналіз може бути неповним</i>")

    return "\n".join(lines)

def send_telegram(text):
    try:
        from alert import API_TOKEN, CHAT_ID
        from telegram import Bot
        bot = Bot(token=API_TOKEN)
        asyncio.run(bot.send_message(chat_id=CHAT_ID, text=text[:4000], parse_mode='HTML'))
        print("Sent to TG")
    except Exception as e:
        print(f"TG error: {e}")

def format_status_update(info):
    """Формує повідомлення про зміну статусу закону."""
    bill_number = info['bill_number']
    title = info['title']
    status = info['status']
    bill_url = info.get('url', '')
    old_value = info.get('old_value', '')
    new_value = info.get('new_value', '')
    reg_date = info.get('reg_date', '')
    committee = info.get('committee', '')

    lines = []
    if bill_url:
        lines.append(f"📜 <b>#{bill_number}</b> — <a href='{bill_url}'>{title[:80]}</a>")
    else:
        lines.append(f"📜 <b>#{bill_number}</b> — {title[:80]}")

    # Попередній та новий статус
    if old_value:
        lines.append(f"🔄 Статус: {old_value} → <b>{new_value}</b>")
    else:
        lines.append(f"📊 Статус: <b>{status}</b>")

    date_str = f" (зареєстровано {reg_date})" if reg_date else ""
    if committee:
        lines.append(f"🏛 Комітет: {committee}{date_str}")
    elif date_str:
        lines.append(f"📅{date_str}")

    return "\n".join(lines)

def mark_notified(bill_ids):
    if not bill_ids:
        return
    c = db(); cur = c.cursor()
    cur.execute("UPDATE change_log SET notified = true WHERE bill_id = ANY(%s)", (bill_ids,))
    c.commit(); cur.close(); c.close()

def process_bill(info):
    bill_id = info['id']
    bill_number = info['bill_number']
    title = info['title']
    status = info['status']
    bill_url = info.get('url', '')
    print(f"\n Processing: #{bill_number} | {title[:60]}")
    print(f"  Status: {status}")

    docs = get_bill_documents(bill_id, bill_number)
    if not docs:
        print("  No documents")
        return None, None

    print(f"  Documents: {len(docs)}")
    rada_token = get_rada_token()
    all_chunks = []
    pdf_hashes = []
    for doc in docs:
        try:
            data = download_rada_pdf(str(doc['file_id']), rada_token)
            if len(data) < 1000:
                continue
            pdf_hash = hashlib.md5(data).hexdigest()
            pdf_hashes.append(pdf_hash)

            # Рання перевірке: після першого PDF
            if len(pdf_hashes) == 1:
                stored_hash = get_stored_hash(bill_id)
                # Обчислюємо поточний combined hash від усіх зібраних PDF
                current_hash = hashlib.md5(''.join(pdf_hashes).encode()).hexdigest()
                if stored_hash and stored_hash == current_hash:
                    print(f"  Early cache hit (hash={current_hash[:8]}) - skipping all")
                    return None, None
            safe_bn = "".join(c if (c.isalnum() or c in "._-") else "_" for c in str(bill_number))
            path = f"/tmp/rag_{safe_bn}_{doc['file_id']}.pdf"
            with open(path, 'wb') as f:
                f.write(data)
            text = extract_pdf_text(path)
            os.unlink(path)
            doc_type = 'zakon' if 'Закону' in doc.get('type', '') else 'poyasn' if 'Пояснювальна' in doc.get('type', '') else 'other'
            chunks = chunk_text(text)
            for i, chunk in enumerate(chunks):
                sec = doc_type
                if 'метою' in chunk[:200].lower():
                    sec = 'meta'
                elif any(w in chunk[:200].lower() for w in ['фінансування', 'бюджет', 'витрат']):
                    sec = 'finance'
                all_chunks.append({'bill_id': bill_id, 'reg_number': bill_number, 'doc_type': doc_type, 'chunk_index': i, 'text': chunk, 'section': sec})
        except Exception as e:
            print(f"  Doc error: {e}")

    if not all_chunks:
        print("  No text extracted")
        return None, None

    # dedup
    uniq, seen = [], set()
    for chunk in all_chunks:
        short = chunk['text'][:120]
        if short not in seen:
            seen.add(short)
            uniq.append(chunk)
    all_chunks = uniq
    # Зберігаємо хеш від усіх PDF даних
    all_pdf_hash = hashlib.md5(''.join(pdf_hashes).encode()).hexdigest() if pdf_hashes else None

    # Перевіряємо чи змінився контент
    stored_hash = get_stored_hash(bill_id)
    if stored_hash and stored_hash == all_pdf_hash:
        print(f"  Cache hit (hash={all_pdf_hash[:8]}) - skipping LLM")
        return None, None

    c = db(); cur = c.cursor()
    cur.execute("DELETE FROM rag_chunks WHERE bill_id = %s", (bill_id,))
    cur.execute("DELETE FROM rag_documents WHERE bill_id = %s", (bill_id,))
    cur.execute("INSERT INTO rag_documents (bill_id, doc_type, title, content_hash) VALUES (%s, %s, %s, %s) RETURNING id", (bill_id, 'combined', f'Закон #{bill_number}', all_pdf_hash))
    doc_db_id = cur.fetchone()[0]
    for chunk in all_chunks:
        cur.execute("INSERT INTO rag_chunks (document_id, bill_id, chunk_index, chunk_text, section) VALUES (%s, %s, %s, %s, %s)",
                    (doc_db_id, chunk['bill_id'], chunk['chunk_index'], chunk['text'][:2000], chunk['section']))
    c.commit(); cur.close(); c.close()
    print(f"  Stored: doc_id={doc_db_id} chunks={len(all_chunks)} pdf_hash={all_pdf_hash[:8]}")

    # Зберігаємо версію закону для історії
    plain_text = '\n\n'.join(c['text'] for c in all_chunks)
    lv_c = db(); lv_cur = lv_c.cursor()
    lv_cur.execute("""
        INSERT INTO law_versions (law_id, status_at_moment, text_hash, plain_text)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (law_id, text_hash) DO NOTHING
        RETURNING id
    """, (bill_id, status, all_pdf_hash, plain_text[:50000]))
    lv_cur.close(); lv_c.commit(); lv_c.close()

    # Оновлюємо bills.text_hash та bills.plain_text
    up_c = db(); up_cur = up_c.cursor()
    up_cur.execute("UPDATE bills SET text_hash=%s, plain_text=%s WHERE id=%s", (all_pdf_hash, plain_text[:50000], bill_id))
    up_c.commit(); up_cur.close(); up_c.close()

    substantive = [c['text'] for c in all_chunks if any(w in c['text'] for w in ['стаття', 'Угода', 'Позик', 'Меморандум', 'фінансов', 'Кредитор', 'Позичальник', 'макрофінансова'])]
    ctx = '\n\n'.join(substantive[:5]) if substantive else '\n\n'.join(c['text'] for c in all_chunks[:3])
    insufficient = len(ctx.strip()) < 1200
    prompt = (
        "Ти — незалежний аналітик законодавства. Проаналізуй текст законопроєкту та вияви ризики для демократії, прав громадян, бюджету та верховенства права.\n"
        "Відповідай українською, ТІЛЬКИ JSON, без додаткового тексту.\n"
        "Будь прагматичним і фактологічним. Не давай політичних оцінок, не використовуй емоційний мову.\n"
        "Кожен ризик підкріплюй точною цитатою з тексту.\n"
        "Якщо текст не містить ризиків — поверни пустий масив risks.\n"
        "Формат відповіді:\n"
        '{\n'
        '  "summary": "3-4 речення: що конкретно змінюється на практиці, які механізми вводяться або скасовуються. Без декларативних преамбул.",\n'
        '  "risks": [\n'
        '    {\n'
        '      "category": "Corruption | Budgetary | Legal Collision | Ambiguity | Civil Rights | Power Concentration | Other",\n'
        '      "severity": "Low | Medium | High",\n'
        '      "quote": "Точна цитата з тексту закону (1-2 речення)",\n'
        '      "explanation": "Об\'єктивне пояснення: чому ця норма є ризиком, які наслідки можуть бути на практиці"\n'
        '    }\n'
        '  ]\n'
        '}\n'
        "Правила:\n"
        "- Не вигадуй наслідки — аналізуй лише наведений текст.\n"
        "- Не використовуй політичні лозунги або емоційні оцінки.\n"
        "- Якщо текст обмежений або неповний — зазнач це в summary.\n"
        "- Кожен ризик має бути підкріплений конкретною цитатою з тексту.\n"
        "- Категорія має бути однією з: Corruption, Budgetary, Legal Collision, Ambiguity, Civil Rights, Power Concentration, Other.\n"
        "- Якщо ризиків не виявлено — поверни [].\n\n"
        "Текст законопроєкту:\n" + ctx
    )

    try:
        data = rag_answer(prompt)
    except Exception as e:
        print(f" LLM_FAIL: {type(e).__name__}: {str(e)[:200]}")
        return None, None

    try:
        save_risk(doc_db_id, data, GROQ_MODEL)
    except Exception as e:
        print(f" SAVE_FAIL: {type(e).__name__}: {str(e)[:200]}")
        return None, None

    # Оновлюємо law_versions з результатами аналізу
    analysis_summary = data.get('summary', '')[:2000]
    risks_json = json.dumps(data) if data else None
    lv_c = db(); lv_cur = lv_c.cursor()
    lv_cur.execute("""
        UPDATE law_versions SET analysis_summary=%s, risks_json=%s
        WHERE law_id=%s AND text_hash=%s
    """, (analysis_summary, risks_json, bill_id, all_pdf_hash))
    lv_c.commit(); lv_cur.close(); lv_c.close()

    return info, data

def main():
    force = '--force' in sys.argv
    test = '--test' in sys.argv
    print(f"=== RAG Monitor {datetime.now()} ===")
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY missing")
        return

    if force:
        c = db(); cur = c.cursor()
        cur.execute("SELECT DISTINCT bill_id FROM change_log WHERE change_type IN ('new','status_change')")
        bill_ids = [r[0] for r in cur.fetchall()]
        cur.close(); c.close()
        bills = []
        for bid in bill_ids[:10]:
            c = db(); cur = c.cursor()
            cur.execute("SELECT id, bill_number, title, current_status, registration_date, committee, agenda_category, url FROM bills WHERE id=%s", (bid,))
            row = cur.fetchone()
            cur.close(); c.close()
            if row:
                bills.append({'id': row[0], 'bill_number': row[1], 'title': row[2], 'status': row[3], 'reg_date': row[4], 'committee': row[5], 'category': row[6], 'change_type': 'new' if not row[3] or row[3]=='new' else 'status_change', 'old_value': None, 'new_value': None, 'url': row[7]})
    else:
        bills_raw = find_bills_needing_rag()
        bills = []
        for r in bills_raw:
            bills.append({'id': r[0], 'bill_number': r[1], 'title': r[2], 'status': r[3], 'reg_date': r[4], 'committee': r[5], 'category': r[6], 'change_type': r[7], 'old_value': r[8], 'new_value': r[9], 'url': r[10] if len(r) > 10 else None})

    print(f"Bills to analyze: {len(bills)}")
    if not bills:
        print("Nothing to do.")
        return

    processed = []  # (info, data) — повний аналіз ризиків
    status_updates = []  # info — тільки зміна статусу
    for bill_info in bills:
        try:
            change_type = bill_info.get('change_type', 'new')
            if change_type == 'status_change':
                # Зміна статусу — повідомляємо без переаналізу LLM
                print(f"\n Status update: #{bill_info['bill_number']} | {bill_info['title'][:60]}")
                print(f"  {bill_info.get('old_value','?')} → {bill_info.get('new_value','?')}")
                status_updates.append(bill_info)
                mark_notified([bill_info['id']])
            else:
                # Новий закон — повний аналіз
                info, data = process_bill(bill_info)
                if data:
                    processed.append((info, data))
                    mark_notified([info['id']])
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()

    # Відправляємо повідомлення
    if not test:
        # Спочатку — зміни статусів
        for info in status_updates:
            msg = format_status_update(info)
            send_telegram(msg)
            print(f"  Sent status update: #{info['bill_number']}")

        # Потім — повний аналіз ризиків
        for info, data in processed:
            msg = format_tg_message(info, data)
            send_telegram(msg)
            print(f"  Sent to TG: #{info['bill_number']}")

    print(f"\n=== Done: {len(processed)} analyzed, {len(status_updates)} status updates ===")

if __name__ == "__main__":
    main()
