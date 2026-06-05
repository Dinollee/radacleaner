# Робочі API ендпоїнти Верховної Ради України (IX скликання)

## Дані про законопроєкти

### Легкий список (15K+ записів, без статусів)
```
GET https://data.rada.gov.ua/ogd/zpr/skl9/billinfo_list-skl9.json
```
Поля: id, registrationNumber, name, registrationDate, rubric, subject, initiators

### Повний список (129MB, зі статусами та етапами)
```
GET https://data.rada.gov.ua/ogd/zpr/skl9/billinfo-skl9.json
```
Поля + currentPhase, documents, passings, adoptions

### Картка закону на сайті ВР
```
GET https://itd.rada.gov.ua/billInfo/Bills/Card/{id}
```
Де id — числовий ID з JSON (наприклад 70146)

## Голосування

### Список голосувань за законом
```
GET https://w1.c1.rada.gov.ua/pls/radan_gs09/ns_zakon_gol_dep_wohf?zn={registrationNumber}
```
Параметр `zn` — номер закону з ведучими нулями (наприклад `0376`)
Повертає HTML з посиланнями на `g_id` (ID голосувань)
✅ Робочі zn: 0371, 0374, 0376

### Деталі голосування (поіменне)
```
GET https://w1.c1.rada.gov.ua/pls/radan_gs09/ns_golos?g_id={g_id}
```
Параметр `g_id` — числовий ID голосування
Повертає HTML з:
- Датою голосування
- Результатами (За/Проти/Утрималися/Не голосували/Відсутні)
- Списком депутатів (ім'я + статус) після `Версія для друку`
- Фракціями (групуються в `<div id="0idfX">`)

✅ Робочі g_id: 34579, 34580, 34780, 34781, 34782, 34783, 34784

### Фракції — парсяться з HTML голосування
На сторінці `ns_golos` фракції знаходяться в:
- Чекбоксах: `<input name="fr" value="idf1">` → назва фракції
- Блоках: `<div id="0idf1">` → список депутатів фракції

Маппінг idf → фракція:
| ID | Фракція | К-сть |
|---|---|---|
| idf1 | СЛУГА НАРОДУ | 225 |
| idf4 | Європейська Солідарність | 21 |
| idf3 | Батьківщина | 23 |
| idf9 | Платформа за життя та мир | 21 |
| idf7 | ДОВІРА | 19 |
| idf8 | Партія "За майбутнє" | 17 |
| idf5 | ГОЛОС | 18 |
| idf10 | Відновлення України | 17 |
| idf0 | Позафракційні | 20 |

## Токен доступу до RADA API
```
GET https://data.rada.gov.ua/api/token
```
Повертає тимчасовий токен для завантаження PDF з data.rada.gov.ua

## Структура голосування (ns_golos)

Сторінка `ns_golos?g_id=X` має таку структуру:
1. **Заголовок**: назва закону, дата голосування
2. **Результати**: `За:231 Проти:4 Утрималися:31 Не голосували:45 Всього:311`
3. **Фракції** (через чекбокси + блоки з id="0idfX")
4. **Список депутатів** (після `<a name="r1">`):
   ```
   Ім'я1
   Статус1
   Ім'я2
   Статус2
   ```
5. **Версія для друку** — простий список імен + статусів

Статуси голосування:
1 — За
2 — Проти
3 — Утримався
4 — Не голосував
5 — Відсутній

## PostgreSQL схема (my_bills)

```sql
CREATE TABLE bills (
    bill_id INTEGER PRIMARY KEY,
    bill_number VARCHAR(20),
    title TEXT,
    introduced_date DATE,
    bill_type VARCHAR(50)
);

CREATE TABLE voting_sessions (
    g_id INTEGER PRIMARY KEY,
    bill_id INTEGER REFERENCES bills(bill_id),
    vote_date TEXT,
    description TEXT,
    results_json TEXT
);

CREATE TABLE deputies (
    id SERIAL PRIMARY KEY,
    full_name TEXT NOT NULL,
    g_id INTEGER REFERENCES voting_sessions(g_id),
    vote_status INTEGER,
    bill_id INTEGER REFERENCES bills(bill_id),
    UNIQUE(full_name, g_id)
);

CREATE TABLE factions (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);

CREATE TABLE deputy_factions (
    deputy_name TEXT NOT NULL,
    faction_id INTEGER REFERENCES factions(id),
    UNIQUE(deputy_name)
);
```

## Відомі закони з голосуваннями

| zn | Bill ID | Назва | Голосувань | Дата |
|---|---|---|---|---|
| 0371 | 14951 | Конвенція про Міжнародну компенсаційну комісію для України | 2 | 30.04.2026 |
| 0374 | 14952 | Грантова угода з Францією (Фонд Україна II) | 2 | 28.05.2026 |
| 0376 | 14950 | Угода про Позику з ЄС (Макрофінансова допомога) | 3 | 28.05.2026 |