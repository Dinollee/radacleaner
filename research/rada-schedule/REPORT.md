# Источники расписания Верховной Рады Украины для календаря radacleaner

> Generated 2026-07-08 · depth: standard · 6 angles · workspace: research/rada-schedule/

## Executive summary

- **meeting.rada.gov.ua** — основной хаб для данных о пленарных заседаниях: календарный план, повестка дня, результаты голосований, хронология [1][2][3]
- **data.rada.gov.ua** — бесплатный Open Data API без авторизации; наборы "zal" (140 датасетов — хронология, голосования) и "meetings" (115 датасетов — повестки, стенограммы) [4][5]
- **static.rada.gov.ua** — статические HTML-страницы с еженедельными графиками комитетов по предсказуемому URL: `RK{DDMMYY}.htm` [6][7]
- **w1.c1.rada.gov.ua** — legacy-система с параметрическими URL для календаря пленарных заседаний на любую дату [8][9]
- **Homepage виджет** (rada.gov.ua) — показывает ближайшие события с точным временем [10]
- **Голосование** — не отдельное событие, а часть пленарного заседания; нет отдельного "режима голосования" [11]
- **Текущая сессия**: 15-я сессия IX скликання, февраль–август 2026 г. [12]

## Источники данных

### 1. meeting.rada.gov.ua — Пленарные заседания

Основной интерфейс для данных о заседаниях.

| Эндпоинт | Данные | URL |
|-----------|--------|-----|
| Календарный план | Дни: пленарное / час вопросов / рабочий / выходной | `meeting.rada.gov.ua/work/main/dayp` |
| Повестка дня | Повестки заседаний | `meeting.rada.gov.ua/work/main/agenda` |
| Результаты голосований | Голосования по законопроектам | `meeting.rada.gov.ua/work/main/sps` |
| Хронология | Хронология рассмотрения | `meeting.rada.gov.ua/work/main/chronology` |
| Все анонсы | Все события (комитеты + пленарные) | `meeting.rada.gov.ua/work/main/dayc` |
| Англ. интерфейс | `?lang=en` | `meeting.rada.gov.ua/work/main/dayp?lang=en` |

**Годовые страницы**: `dayp2026`, `dayp9_15` (15-я сессия IX скликання).

### 2. data.rada.gov.ua — Open Data API

Бесплатный REST API, авторизация не требуется (анонимный доступ).

**Основные наборы данных**:

| Набор | Содержимое | Датасетов |
|-------|-----------|-----------|
| `zal` | Хронология рассмотрения вопросов повестки, результаты голосований | 140 |
| `meetings` | Повестки, стенограммы, погоджувальні ради, оперативна інформація | 115 |

**Формат**: CSV, JSON, XML (через content-negotiation: добавить `.csv`, `.json`, `.xml` к ID датасета).

**Примеры URL**:
- Список датасетов `zal`: `data.rada.gov.ua/ogd/zal/list.json`
- Датасет meetings: `data.rada.gov.ua/open/data/meetings`

**Обновление**: ежечасно. Покрывает все скликання (III–IX, 1998–н.в.).

### 3. static.rada.gov.ua — Еженедельные графики

Статические HTML-страницы, обновляемые вручную.

**Графики комитетов**:
- Индекс: `static.rada.gov.ua/zakon/new/RK/index.htm`
- Файл: `RK{DD}{MM}{YY}.htm` (пример: `RK290626.htm` — неделя 29.06–03.07.2026)
- Содержимое: название комитета, дата, время, зал, повестка, номера законопроектов

**Графики пленарных заседаний** (legacy):
- Индекс: `static.rada.gov.ua/zakon/new/WR/index.htm`
- Файл: `WR{DDMMYY}.htm`
- Примечание: отстаёт от актуальности (показывает сессию 7, 2022)

### 4. w1.c1.rada.gov.ua — Legacy календарь

Параметрический endpoint для расписания на конкретную дату.

**URL**: `w1.c1.rada.gov.ua/pls/radan_gs09/ns_h2?day_={DD}&month_={MM}&year={YYYY}&nom_s=15`

`nom_s=15` — номер сессии (текущая: 15-я, IX скликання).

**Содержимое**: пленарные заседания с результатами голосований (за/проти/утримались/не голосували/відсутні).

### 5. Homepage виджет «Календар подій»

На главной странице rada.gov.ua виджет показывает ближайшие события:
- Заседания комитетов с точным временем
- Ссылки на анонсы: `/preview/anonsy_podij/{id}.html`
- ID анонсов последовательные (с пропусками): 274035, 274034, 274059...

### 6. Комитеты

23 комитета IX скликання, каждый с уникальным slug:
- `komagropolit.rada.gov.ua` (аграрная политика)
- `komekolog.rada.gov.ua` (экология)
- `komzdrav.rada.gov.ua` (здравоохранение)
- Полный список: `people.rada.gov.ua/go/vr-kom`

## Сессии и периоды работы

| Параметр | Значение |
|----------|----------|
| Текущая сессия | 15-я, IX скликання |
| Период | Февраль–Август 2026 |
| Паттерн | Весна (фев–авг) / Осень (сен–янв) |
| Основание | Постанова ВРУ 2912-IX від 07.02.2023 (змінена 3279-IX від 27.07.2023) |
| Конституция | Ст. 82: первая сессия фев–июль, вторая сен–дек |
| Позачергові сесії | Возможны (июль 2020, август 2020, август 2021) |

## Голосование

- Голосование происходит **в рамках пленарного заседания** — нет отдельных "сессий голосования"
- Законопроекты проходят **3 чтения**; голосование на каждом этапе
- Типичный объём: ~200 законов/год, 5–6 регистраций/день
- В военное время: 90% первых чтений — за <2 минут; среднее время до второго чтения выросло до 335 дней

## Рекомендуемая стратегия интеграции для календаря

**Приоритет 1** (структурированные данные):
1. `data.rada.gov.ua` — JSON/CSV API для планирования и результатов
2. `meeting.rada.gov.ua/work/main/dayp2026` — календарный план с типами дней

**Приоритет 2** (HTML-скрейпинг):
3. `static.rada.gov.ua/zakon/new/RK/RK{DDMMYY}.htm` — еженедельные графики комитетов
4. Homepage виджет — ближайшие события с временем

**Приоритет 3** (legacy/fallback):
5. `w1.c1.rada.gov.ua/pls/radan_gs09/ns_h2` — проверка по дате

## Open questions

1. Есть ли RSS/ICS feed на meeting.rada.gov.ua для автоматической синхронизации календаря?
2. Как структурированы данные в `zal` и `meetings` датасетах — какие поля доступны для календарных событий?
3. Обновляются ли данные на data.rada.gov.ua в реальном времени или только раз в сутки?
4. Какой формат данных у homepage виджета — можно ли его парсить без headless browser?

## Sources

[1] meeting.rada.gov.ua — Календарний план сесій — https://meeting.rada.gov.ua/work/main/dayp (accessed 2026-07-08)
[2] meeting.rada.gov.ua — Порядок денний засідань — https://meeting.rada.gov.ua/work/main/agenda (accessed 2026-07-08)
[3] meeting.rada.gov.ua — Результати голосувань — https://meeting.rada.gov.ua/work/main/sps (accessed 2026-07-08)
[4] data.rada.gov.ua — Open Data API — https://data.rada.gov.ua/open/main/api (accessed 2026-07-08)
[5] data.rada.gov.ua — Dataset zal — https://data.rada.gov.ua/open/data/zal (accessed 2026-07-08)
[6] static.rada.gov.ua — Тижневі графіки засідань комітетів — http://static.rada.gov.ua/zakon/new/RK/index.htm (accessed 2026-07-08)
[7] static.rada.gov.ua — Пример: RK290626.htm — http://static.rada.gov.ua/zakon/new/RK/RK290626.htm (accessed 2026-07-08)
[8] w1.c1.rada.gov.ua — План пленарних засідань — https://w1.c1.rada.gov.ua/pls/radan_gs09/ns_h2?day_=08&month_=07&year=2026&nom_s=15 (accessed 2026-07-08)
[9] w1.c1.rada.gov.ua — Календар сесій — https://w1.c1.rada.gov.ua/pls/radan_gs09/ns_el_h (accessed 2026-07-08)
[10] rada.gov.ua — Календар подій (виджет) — https://www.rada.gov.ua/ (accessed 2026-07-08)
[11] rada.gov.ua — Порядки денні пленарних засідань — https://www.rada.gov.ua/en/meeting/awt/show/8359.html (accessed 2026-07-08)
[12] Постанова ВРУ 2912-IX від 07.02.2023 — https://meeting.rada.gov.ua/work/main/dayp9_15 (accessed 2026-07-08)
[13] data.rada.gov.ua — meetings dataset — https://data.rada.gov.ua/open/data/meetings (accessed 2026-07-08)
[14] people.rada.gov.ua — Комітети ВРУ — https://people.rada.gov.ua/go/vr-kom (accessed 2026-07-08)
[15] OpenAustralia scraper — https://github.com/openaustralia/ukraine_verkhovna_rada_votes (accessed 2026-07-08)
