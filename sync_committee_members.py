#!/usr/bin/env python3
"""
Синхронізація членів комітетів з RADA API.
Зберігає інформацію про належність депутатів до комітетів.
"""
import urllib.request
import json
import http.cookiejar
import psycopg2
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def get_db():
    return psycopg2.connect(
        host=os.getenv("DB_HOST"), port=os.getenv("DB_PORT", "5432"),
        dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
    )


def fetch_deputy_data():
    """Отримати дані про депутатів з RADA API."""
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", "Mozilla/5.0")]

    # Завантажуємо головну сторінку для отримання cookies
    opener.open("https://itd.rada.gov.ua/struct/uk/Structure/MPs").read()

    # Отримуємо список депутатів
    url = "https://itd.rada.gov.ua/struct/Data/UserNames/?pageId=3&culture=uk"
    resp = opener.open(url)
    return json.loads(resp.read().decode("utf-8"))


def sync_committees():
    """Синхронізувати членів комітетів."""
    print("Отримання даних з RADA API...")
    deputies = fetch_deputy_data()
    print(f"Отримано {len(deputies)} депутатів")

    # Групуємо по комітетах
    committees = {}
    for d in deputies:
        comm_id = d.get("CommId")
        if not comm_id:
            continue

        name = d.get("FullName", "")
        post_name = d.get("PostComName", "") or ""

        # Визначаємо роль
        if "Голова" in post_name and "Заступник" not in post_name and "підкомітету" not in post_name:
            role = "chair"
        elif "Голова підкомітету" in post_name:
            role = "subcommittee_head"
        elif "Заступник голови" in post_name:
            role = "vice_chair"
        elif "Секретар" in post_name:
            role = "secretary"
        else:
            role = "member"

        committees.setdefault(comm_id, []).append({
            "name": name,
            "uid": d["UserId"],
            "role": role,
            "post_name": post_name,
        })

    print(f"\nЗнайдено {len(committees)} комітетів")

    # Зберігаємо в БД
    conn = get_db()
    cur = conn.cursor()

    # Очищуємо старі дані
    cur.execute("DELETE FROM committee_members")
    print("Очищено старі дані")

    # Вставляємо нові
    total = 0
    for comm_id, members in committees.items():
        for m in members:
            cur.execute(
                "INSERT INTO committee_members (committee_id, member_name, member_uid, role, committee_name) VALUES (%s, %s, %s, %s, %s)",
                (comm_id, m["name"], m["uid"], m["role"], m.get("post_name", "")),
            )
            total += 1

    conn.commit()
    print(f"Збережено {total} записів")

    # Статистика
    cur.execute("""
        SELECT committee_id, COUNT(*) as members,
               COUNT(CASE WHEN role = 'chair' THEN 1 END) as chairs
        FROM committee_members
        GROUP BY committee_id
        ORDER BY members DESC
    """)
    print("\nСтатистика по комітетах:")
    for row in cur.fetchall():
        print(f"  Committee {row[0]}: {row[1]} members, {row[2]} chairs")

    cur.close()
    conn.close()
    print("\nГотово!")


if __name__ == "__main__":
    sync_committees()
