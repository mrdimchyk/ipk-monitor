# VOBU IPK Scraper v1.1

Автоматичний збір нових індивідуальних податкових консультацій з VOBU.

## Поточна логіка

- VOBU listing -> HTML parsing
- тільки `/view/` посилання
- дедуплікація за URL
- SQLite state
- 7-денне overlap-вікно
- завантаження повного тексту нових ІПК
- plain-text + HTML email
- GitHub Actions daily run

## Важливе

Під час перевірки 25.08.2026 веб-джерело VOBU показувало ІПК від 21.08.2026; окремі detail pages також доступні як HTML і містять дату, номер та повний текст. Scraper не припускає, що номер ІПК йде послідовно.

## Локальний тест

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scraper.py --max-pages 2
```

Перевірка бази:

```bash
python - <<'PY'
import sqlite3
db=sqlite3.connect("ipk.sqlite3")
for row in db.execute("select ipk_date, number, source_url from ipk order by ipk_date desc limit 20"):
    print(row)
PY
```

## Email

Для Gmail використовуйте App Password.

Задайте:

- SMTP_HOST
- SMTP_PORT
- SMTP_USER
- SMTP_PASSWORD
- MAIL_TO

Потім:

```bash
python scraper.py --send-email
```

## GitHub Actions

Додайте ці 5 значень у Repository -> Settings -> Secrets and variables -> Actions.

Workflow можна запустити вручну через `Run workflow`.

## Що ще треба зробити перед production

1. Прогнати scraper на живому VOBU.
2. Зіставити кількість записів із сайтом.
3. Перевірити 20 detail pages.
4. Перевірити дублікати.
5. Перевірити пропуски при симуляції 3 днів без запуску.
6. Додати окреме поле `published_at`/`document_date` та `discovered_at`.
7. Додати контроль змін уже зібраного документа.
8. Додати тематичну класифікацію.
9. Додати короткий зміст у лист.
