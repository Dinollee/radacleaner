# Report: EU Negotiation Clusters — Stable Data Sources

**Date:** 2026-07-08
**Question:** Where to get stable, machine-readable data about EU negotiation clusters for Ukraine?

---

## Executive Summary

**Єдиного офіційного API для статусу кластерів не існує.** Найкращий підхід — комбінація RSS-фідів + скрапінг урядових порталів + моніторинг новин.

### ТОП-3 джерела для автоматизації

1. **European Pravda** (eurointegration.com.ua) — ламає ексклюзиви про відкриття кластерів
2. **eu-ua.kmu.gov.ua** — урядовий портал з новинами про переговори
3. **pulse.kmu.gov.ua** — моніторинг виконання Угоди (24 напрямки)

---

## Detailed Findings

### 1. EU Council (consilium.europa.eu)

**Стан:** RSS заблоковані (403). API немає.

| Що | URL | Автоматизація |
|----|-----|---------------|
| Прес-релізи | consilium.europa.eu/en/press | ❌ 403 |
| IGC результати | consilium.europa.eu | ❌ Ручний перегляд |
| RSS | /en/rss/?terms=Ukraine | ❌ 403 |

**Висновок:** Не підходить для автоматизації.

### 2. European Commission (ec.europa.eu)

**Стан:** RSS працюють з фільтрацією по країні.

| Що | URL | Автоматизація |
|----|-----|---------------|
| **News RSS (Ukraine)** | enlargement.ec.europa.eu/node/2/rss_en?f[0]=country_country:UKR | ✅ RSS |
| Documents RSS | enlargement.ec.europa.eu/node/3436/rss_en | ✅ RSS |
| Country page | enlargement.ec.europa.eu/countries/ukraine_en | ⚠️ Скрапінг |
| State of Play PDF | enlargement.ec.europa.eu/document/download/... | ⚠️ PDF parsing |
| SPARQL | data.europa.eu/data/sparql | ✅ (немає даних по кластерах) |

**Висновок:** RSS працює для новин. State of Play PDF — джерело truth для статусу глав, але потрібен PDF parser.

### 3. Ukrainian Government

**Стан:** Найкраще джерело — eu-ua.kmu.gov.ua + pulse.kmu.gov.ua

| Що | URL | Автоматизація |
|----|-----|---------------|
| **eu-ua.kmu.gov.ua/news** | /areas/accession-ukraine-to-eu/ | ✅ CMS скрапінг |
| **pulse.kmu.gov.ua** | pulse.kmu.gov.ua | ✅ API/скрапінг |
| MFA | mfa.gov.ua | ❌ 403 Blocked |
| RADA | rada.gov.ua/en/news | ✅ RSS (/en/rss/news) |

**Ключове:** Заступник Міністра — **Тарас Качка** (не Стефанішина). План "Качка-Кос" (грудень 2025).

**pulse.kmu.gov.ua** — моніторинг 24 напрямків асоціації. Показує хто відповідає за кожне завдання. Найкращий кандидат для автоматизації.

### 4. Third-party Trackers

**Стан:** Відкритих трекерів НЕМАЄ.

| Що | URL | Автоматизація |
|----|-----|---------------|
| CEPS | ceps.eu | ❌ Тільки PDF-звіти (83 публікації) |
| EBRD | ebrd.com | ❌ Інвестиційні дані (€23.9B), не переговори |
| Bertelsmann Stiftung | bertelsmann-stiftung.de | ❌ 404 — трекер не знайдено |
| GitHub repos | github.com | ❌ 0 репозиторіїв по EU enlargement tracker |
| Академічні дашборди | — | ❌ Тільки PDF |

**Висновок:** Жоден think tank чи NGO не підтримує відкритий machine-readable трекер кластерів. Всі дані — в PDF звітах.

### 5. News Sources

**Стан:** European Pravda — головне джерело ексклюзивів.

| Що | URL | RSS | Автоматизація |
|----|-----|-----|---------------|
| **European Pravda** | eurointegration.com.ua | ❌ (404) | ✅ Скрапінг |
| **Ukrainska Pravda** | pravda.com.ua | ✅ /rss/view_news/ | ✅ RSS |
| Ukrinform | ukrinform.net | ❌ (404) | ❌ |
| EC Press Corner | ec.europa.eu/commission/presscorner | ❌ | ⚠️ Скрапінг |

**European Pravda** — ламає ексклюзиви (наприклад, 08.07.2026: "коли відкриють наступний кластер"). RSS немає, але статті мають передбачуваний URL pattern: `/news/YYYY/MM/DD/{id}/`.

**Ukrainska Pravda** — RSS працює, але EU-кластери з'являються тільки при великих новинах.

---

## Recommended Architecture

```
┌─────────────────────────────────────────────────────┐
│                    EU Cluster Tracker                │
├─────────────────────────────────────────────────────┤
│                                                     │
│  1. EC RSS Monitor (раз на день)                    │
│     └─ enlargement.ec.europa.eu/node/2/rss_en      │
│        ?f[0]=country_country:UKR                    │
│     └─ Парсинг заголовків на "cluster" keyword     │
│                                                     │
│  2. European Pravda Scraper (раз на день)           │
│     └─ eurointegration.com.ua/news/                 │
│     └─ Пошук "кластер" в заголовках                │
│     └─ Збереження в change_log                     │
│                                                     │
│  3. Government Portal Scraper (раз на тиждень)      │
│     └─ eu-ua.kmu.gov.ua/news/                      │
│     └─ pulse.kmu.gov.ua (якщо API)                 │
│                                                     │
│  4. State of Play PDF Parser (раз на місяць)        │
│     └─ enlargement.ec.europa.eu/document/download/  │
│     └─ PDF → JSON extraction                        │
│                                                     │
└─────────────────────────────────────────────────────┘
```

---

## Open Questions

1. **pulse.kmu.gov.ua** — чи є API? Потрібен окремий досліджувальний запит.
2. **European Pravda** — чи можна знайти RSS через Substack або Newsletter?
3. **State of Play PDF** — яка структура? Чи є machine-readable версія?

---

## Sources

1. [1] enlargement.ec.europa.eu/countries/ukraine_en — verified 2026-07-08
2. [2] eu-ua.kmu.gov.ua — verified 2026-07-08
3. [3] pulse.kmu.gov.ua — verified 2026-07-08
4. [4] eurointegration.com.ua — verified 2026-07-08
5. [5] pravda.com.ua/rss/view_news/ — verified 2026-07-08
6. [6] Wikipedia: Accession of Ukraine to the EU — accessed 2026-07-08
7. [7] EC 2025 Enlargement Package — Nov 2025
8. [8] State of Play factsheet — 17 Mar 2026
