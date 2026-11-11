# IPK Monitor — reliable daily IPK collection

GitHub Actions collects newly published tax consultations from VOBU, retains
their full text in SQLite and sends an email digest when new records exist.
The separate Google Apps Script in `google-apps-script/Code.gs` only manages
subscribers. A legacy Google Apps Script digest may still be deployed outside
this repository and must be checked in the Apps Script console.

## Schedule and monitoring

- GitHub Actions schedule: **05:30 UTC** (about 07:30 in winter and
  08:30 in summer in Kyiv). GitHub cron executions **can be delayed by
  hours**; it does not offer an exact-time delivery guarantee.
- `verify` runs regression tests before collection.
- `collect` scans 8 pages by default and processes up to 120 new records
  per run. A dated consultation published late is not excluded because its
  document date is old.
- Connection timeouts and transient HTTP failures are retried up to 3 times.
- New consultations go into a durable SQLite email outbox. Only after the
  SMTP server accepts a batch of up to 50 is its `emailed_at` saved.
- SQLite changes are committed even if the collector or SMTP step fails, so
  the pending outbox is not discarded.
- `report` creates or updates a public GitHub Issue titled
  **IPK Monitor: collector failed** when a production run fails,
  and closes it after recovery. This is NOT proof that a message reached
  the recipient's inbox.
- No new consultations and no pending mail => no daily newsletter.

See [GitHub Actions](https://github.com/mrdimchyk/ipk-monitor/actions)
and [open alerts](https://github.com/mrdimchyk/ipk-monitor/issues).

## One-time migration of existing state

Legacy rows have no verified email delivery status. On first upgraded run,
they are marked `emailed_at='legacy-unverified'`, **not** re-sent in a large
duplicate mailing. New rows are `NULL` until SMTP acceptance. We cannot
assert historical delivery just because a legacy row is present in SQLite.

Investigate earlier missing correspondence separately by comparing sent
mail for the SMTP account and the dates/records in the database.

## Recipient configuration — still needs verification

If `SUBSCRIBERS_URL` is configured, `SUBSCRIBERS_TOKEN` is required and
the Google Apps Script endpoint supplies active addresses. Otherwise
`MAIL_TO` is used as a **static fallback** and the subscribers sheet is
not consulted. Logs identify which mode is used without printing email
addresses or secrets.

For production:
1. Verify the intended recipient address in `MAIL_TO` using Repository
   Settings -> Secrets and variables -> Actions. Secrets cannot be read back.
2. Optionally configure `SUBSCRIBERS_URL` and `SUBSCRIBERS_TOKEN` for
   the subscriber web app, and inspect its active list.
3. Run `IPK daily collector` manually with `check_subscribers=true`.
4. Test delivery with `test_email=true`; confirm receipt in the *actual*
   destination mailbox and check Spam, All Mail, and Sent.
5. Only once GitHub delivery is verified, remove old Google Apps Script
   mail triggers to prevent duplicate messages.

**The connector used in ChatGPT may be linked to a different Gmail
account**; checking one mailbox is not proof of delivery to another.

## Local tests and examples

```bash
pip install -r requirements.txt
python -m unittest discover -s tests -v

# Fetch newly published IPKs, do not send mail:
python scraper.py --max-pages 8 --max-new-details 120

# Send any pending outbox messages and new consultations:
python scraper.py --max-pages 8 --max-new-details 120 --send-email

# Inspect configured subscribers without sending:
python scraper.py --check-subscribers
```

Read pending and legacy statuses:

```bash
python - <<'PY'
import sqlite3
db = sqlite3.connect('ipk.sqlite3')
for status, n in db.execute("""
    select coalesce(emailed_at, 'PENDING') as delivery, count(*)
    from ipk group by delivery order by count(*) desc limit 15
"""):
    print(status, n)
PY
```

## Remaining upstream risk

The public VOBU mirror `peredplata.vobu.ua` has shown a TLS hostname
mismatch. The scraper currently uses a *restricted* certificate-check
workaround only for that known hostname; its use is logged. Replace it
with a verified host or trusted upstream integration when the source is
fixed. Do not extend insecure TLS to other hosts.

VOBU is a secondary publication resource. For substantive legal
interpretations, check the original official tax authority source.


## IPK descriptions and monitoring mailbox (November 11 fix)

- The GitHub digest now includes the consultation number, original document
  date, category, a short **source-based question excerpt**, a selected
  concluding excerpt where present, and the direct source link.
- This is **not a Gemini/AI interpretation**. It must not be presented as
  legal advice, an official conclusion applying to other taxpayers, or proof
  that the consulted authority changed the general law.
- The dedicated monitoring mailbox `ipk.monitor.ua@gmail.com` is copied
  on emails in addition to `MAIL_TO` and any authenticated subscriber
  endpoint recipients. Duplicates are removed.
- If the subscription service is unavailable, a warning is logged and
  delivery uses the known fallback plus monitoring mailbox.
- On the first production run of this version, the collector sends a
  one-time, separately labeled **descriptive recap** of recently
  delivered link-only records (up to 200 in 36 hours, batches of 50).
  Accepted recap batches are recorded to avoid replay on a partial failure.
  After completion, a SQLite marker disables all future recap mail.
- New items have descriptions immediately in their normal digest even if
  Gemini is unavailable.
- The legacy Google Apps Script with Gemini summarization is **not**
  deployed or changed by this GitHub repository. The separately deployed
  Apps Script project and its execution logs require access to that project.
  Do not delete its triggers until the source-based digest is acceptable.

Email acceptance by SMTP is not identical to inbox delivery. Check the
monitoring mailbox's inbox and spam after the rollout.

## Gemini — описи ІПК (з 11.11.2026)

У `Settings → Secrets and variables → Actions → Repository secrets` додайте
`GEMINI_API_KEY`. Значення ключа не розміщується у вихідному коді чи листах.
Програмна інтеграція використовує Google Gemini REST `generateContent` із
моделлю `gemini-3.1-flash-lite` (змінна `GEMINI_MODEL` у workflow).

Для кожної консультації лист містить **питання платника, позицію ДПС,
практичне значення для підприємства та посилання на першоджерело**.
Позначка `(Gemini)` чітко відрізняє модельний виклад від фрагментів
першоджерела. Це інформаційний огляд, а не офіційна консультація.

Система робить групові запити по 5 документів, обмежує кількість викликів
за запуск (типово 16 на партію до 50 ІПК), використовує кеш у SQLite і
переходить на фрагменти першоджерела в разі помилки, ліміту, відсутності
ключа або недоступного тексту. **Ключ не виводиться в журнали.**

Для перевірки ключа без email відкрийте
[GitHub Actions](https://github.com/mrdimchyk/ipk-monitor/actions/workflows/ipk.yml),
оберіть `Run workflow`, поставте `test_gemini=true` і перевірте рядок
`[GEMINI] API test successful` у журналі. Важливо: звичайні запуски можуть
надсилати лист із фрагментами джерела навіть коли Gemini недоступний.

До розсилки включено `ipk.monitor.ua@gmail.com` незалежно від
старого `MAIL_TO`, тому обидві адреси можуть одержувати копії. Для
остаточного виключення старого адресата змініть або видаліть `MAIL_TO`
після підтвердження отримання листа на контрольній скриньці.

Відновлювальні листи з описами консультацій, які нещодавно отримали лише
посилання, надсилаються один раз, без повтору за кожного запуску; їхній
статус зберігається в SQLite.
