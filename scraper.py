#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import sqlite3
import sys
import time
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, date, timedelta, timezone
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse
from html import escape

import requests
from bs4 import BeautifulSoup
import urllib3

BASE_URL = "https://peredplata.vobu.ua/"
PRIMARY_URL = "https://ipk.vobu.ua/"
MIRROR_HOST = "peredplata.vobu.ua"
DATE_RE = re.compile(r"\b(\d{2}\.\d{2}\.\d{4})\b")
IPK_RE = re.compile(r"\d{1,6}/ІПК/[0-9A-ZА-ЯІЇЄҐ-]+(?:-[0-9A-ZА-ЯІЇЄҐ]+)*(?:\s+ІПК)?", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ipk (
  id INTEGER PRIMARY KEY,
  source_url TEXT NOT NULL UNIQUE,
  source_id TEXT,
  number TEXT,
  ipk_date TEXT,
  issuer TEXT,
  category TEXT,
  title TEXT,
  content TEXT,
  content_hash TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  emailed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ipk_date ON ipk(ipk_date);
CREATE INDEX IF NOT EXISTS idx_ipk_number ON ipk(number);
CREATE TABLE IF NOT EXISTS monitor_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

@dataclass
class Record:
    url: str
    source_id: str
    number: str
    ipk_date: str
    issuer: str
    category: str
    title: str = ""
    content: str = ""

class Scraper:
    def __init__(self, db_path="ipk.sqlite3", timeout=30, delay=0.2, allow_insecure_mirror=True):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self._initialize_outbox()
        self.delay = delay
        self.allow_insecure_mirror = allow_insecure_mirror
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; IPK-Digest/2.2)",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
        })
        self.timeout = timeout
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _initialize_outbox(self):
        """Do not resend the entire legacy archive on first deployment.

        Pre-existing emailed_at=NULL rows were collected by an old version that
        did not track delivery. Their delivery is UNVERIFIED, not proven.
        New records are inserted after this one-time migration.
        """
        marker = self.db.execute(
            "SELECT value FROM monitor_meta WHERE key='outbox_v1_initialized'"
        ).fetchone()
        if marker is None:
            count = self.db.execute(
                "SELECT COUNT(*) FROM ipk WHERE emailed_at IS NULL"
            ).fetchone()[0]
            self.db.execute(
                "UPDATE ipk SET emailed_at='legacy-unverified' WHERE emailed_at IS NULL"
            )
            self.db.execute(
                "INSERT INTO monitor_meta (key, value) VALUES ('outbox_v1_initialized', ?)",
                (datetime.now(timezone.utc).isoformat(),),
            )
            self.db.commit()
            if count:
                print(
                    f"[MIGRATION] {count} existing rows marked legacy-unverified; "
                    "historical email delivery must be audited separately.",
                    file=sys.stderr,
                )

    def get(self, url):
        host = urlparse(url).hostname or ""
        verify = not (host == MIRROR_HOST and self.allow_insecure_mirror)
        if not verify and not getattr(self, "_warned_insecure_mirror", False):
            self._warned_insecure_mirror = True
            print(
                "[WARN] Mirror TLS hostname mismatch: certificate validation is "
                "disabled ONLY for the known peredplata.vobu.ua host. "
                "Fix the upstream certificate / move to a trusted source.",
                file=sys.stderr,
            )
        for attempt in range(3):
            try:
                response = self.session.get(url, timeout=self.timeout, verify=verify)
                response.raise_for_status()
                response.encoding = response.apparent_encoding or response.encoding
                return response
            except (requests.exceptions.Timeout,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.HTTPError) as exc:
                if isinstance(exc, requests.exceptions.HTTPError):
                    status = exc.response.status_code if exc.response is not None else 0
                    if status not in (429, 500, 502, 503, 504):
                        raise
                if attempt >= 2:
                    raise
                pause = 2 ** (attempt + 1)
                print(f"[RETRY] {type(exc).__name__} on {host}; "
                      f"attempt {attempt + 2}/3 in {pause}s", file=sys.stderr)
                time.sleep(pause)

    @staticmethod
    def clean(s):
        return re.sub(r"[ \\t\\r\\f\\v]+", " ", s or "").strip()

    @staticmethod
    def iso_date(value):
        return datetime.strptime(value, "%d.%m.%Y").date().isoformat()

    def parse_listing(self, html, page_url):
        soup = BeautifulSoup(html, "html.parser")
        out, seen = [], set()
        for a in soup.select('a[href*="/view/"]'):
            href = urljoin(page_url, a.get("href", ""))
            if href in seen:
                continue
            seen.add(href)
            row = a.find_parent("tr")
            if not row:
                continue
            cells = [self.clean(c.get_text(" ", strip=True)) for c in row.find_all(["td", "th"])]
            row_text = " | ".join(cells)
            dm = DATE_RE.search(row_text)
            if not dm:
                continue
            link_text = self.clean(a.get_text(" ", strip=True))
            nm = IPK_RE.search(link_text) or IPK_RE.search(row_text)
            number = nm.group(0) if nm else link_text
            issuer = cells[2] if len(cells) > 2 else ""
            category = cells[3] if len(cells) > 3 else ""
            tail = urlparse(href).path.rstrip("/").split("/")[-1]
            source_id = tail.split("-", 1)[0]
            out.append(Record(href, source_id, number, self.iso_date(dm.group(1)), issuer, category))
        return out

    def parse_detail(self, html, rec):
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        if h1:
            rec.title = self.clean(h1.get_text(" ", strip=True))
        for tag in soup.select("script, style, nav, footer, form, button"):
            tag.decompose()
        candidates = soup.select("main, article") or soup.find_all(["div", "section"])
        best = max(candidates, key=lambda x: len(x.get_text(" ", strip=True)), default=soup.body)
        text = best.get_text("\n", strip=True) if best else ""
        lines = []
        for line in text.splitlines():
            line = self.clean(line)
            if line:
                lines.append(line)
        filtered = []
        for line in lines:
            if line.startswith("© газета"):
                break
            if line in {"Надіслати", "X"}:
                continue
            filtered.append(line)
        rec.content = "\n".join(filtered).strip()
        return rec

    def exists(self, rec):
        row = self.db.execute(
            "SELECT 1 FROM ipk WHERE source_url=? OR source_id=? OR number=? LIMIT 1",
            (rec.url, rec.source_id, rec.number),
        ).fetchone()
        return row is not None

    def save(self, rec):
        now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        digest = hashlib.sha256(rec.content.encode("utf-8")).hexdigest()
        self.db.execute("""
        INSERT INTO ipk (source_url, source_id, number, ipk_date, issuer, category, title, content, content_hash, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_url) DO UPDATE SET
          source_id=excluded.source_id, number=excluded.number, ipk_date=excluded.ipk_date,
          issuer=excluded.issuer, category=excluded.category, title=excluded.title,
          content=CASE WHEN excluded.content <> '' THEN excluded.content ELSE ipk.content END,
          content_hash=CASE WHEN excluded.content <> '' THEN excluded.content_hash ELSE ipk.content_hash END,
          last_seen_at=excluded.last_seen_at
        """, (rec.url, rec.source_id, rec.number, rec.ipk_date, rec.issuer, rec.category, rec.title, rec.content, digest, now, now))
        self.db.commit()

    def pending_records(self):
        """Persistently retry mail that has not been accepted by SMTP."""
        rows = self.db.execute(
            """SELECT source_url, source_id, number, ipk_date, issuer,
                      category, title, content
               FROM ipk WHERE emailed_at IS NULL
               ORDER BY first_seen_at, id"""
        ).fetchall()
        return [Record(*row) for row in rows]

    def mark_emailed(self, records):
        if not records:
            return
        accepted_at = datetime.now(timezone.utc).isoformat()
        self.db.executemany(
            "UPDATE ipk SET emailed_at=? WHERE source_url=? AND emailed_at IS NULL",
            [(accepted_at, item.url) for item in records],
        )
        self.db.commit()

    def latest_date(self):
        row = self.db.execute("SELECT MAX(ipk_date) FROM ipk").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def collect(self, max_pages=8, overlap_days=7, max_new_details=120):
        # Scan all configured pages: a late-published IPK may have an older document date.
        # overlap_days is retained solely for compatibility with older CLI callers.
        found = []
        for page in range(1, max_pages + 1):
            url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
            print(f"[LIST] {url}", file=sys.stderr)
            response = self.get(url)
            records = self.parse_listing(response.text, response.url)
            print(f"[LIST] parsed={len(records)}", file=sys.stderr)
            if not records:
                if page == 1:
                    raise RuntimeError('No IPK entries parsed from first listing page')
                break
            for rec in records:
                if self.exists(rec):
                    continue
                if len(found) >= max_new_details:
                    break
                print(f"[NEW] {rec.ipk_date} | {rec.number}", file=sys.stderr)
                time.sleep(self.delay)
                detail = self.get(rec.url)
                rec = self.parse_detail(detail.text, rec)
                self.save(rec)
                found.append(rec)
            if len(found) >= max_new_details:
                break
        return found

def digest_html(records):
    today = date.today().strftime("%d.%m.%Y")
    if not records:
        return f"<!doctype html><html><body><h2>ІПК — {today}</h2><p>Нових ІПК не знайдено.</p></body></html>"
    parts = ["<!doctype html><html><body>", f"<h2>Нові ІПК — {today}</h2>", f"<p><b>Кількість: {len(records)}</b></p><ul>"]
    for r in records:
        parts.append(f'<li><b>{escape(r.ipk_date)}</b> — <a href="{escape(r.url)}">{escape(r.number)}</a></li>')
    parts.append("</ul></body></html>")
    return "".join(parts)

def digest_text(records):
    if not records:
        return "Нових ІПК не знайдено."
    return "\n".join([f"Нових ІПК: {len(records)}", ""] + [f"{r.ipk_date} | {r.number} | {r.url}" for r in records])

def get_recipients():
    subscribers_url = os.getenv("SUBSCRIBERS_URL", "").strip()
    subscribers_token = os.getenv("SUBSCRIBERS_TOKEN", "").strip()
    legacy = [x.strip() for x in os.getenv("MAIL_TO", "").split(",") if x.strip()]

    if subscribers_url:
        if not subscribers_token:
            raise RuntimeError("SUBSCRIBERS_URL is set but SUBSCRIBERS_TOKEN is missing")
        try:
            r = requests.get(
                subscribers_url,
                params={"action": "list", "token": subscribers_token},
                timeout=20,
                headers={"User-Agent": "IPK-Digest/2.3"},
            )
            r.raise_for_status()
            data = r.json()
            if not data.get("ok"):
                raise RuntimeError("Subscriber service rejected the request")
            recipients = [str(x).strip() for x in data.get("subscribers", []) if str(x).strip()]
            recipients.extend(legacy)
            recipients = list(dict.fromkeys(recipients))
            print(f"[SUBSCRIBERS] active={len(recipients)}")
            return recipients
        except Exception as exc:
            raise RuntimeError(f"Unable to load subscribers: {exc}") from exc

    if legacy:
        print(f"[SUBSCRIBERS] using MAIL_TO fallback: {len(legacy)}")
        return list(dict.fromkeys(legacy))
    raise RuntimeError("No recipients configured. Set SUBSCRIBERS_URL/SUBSCRIBERS_TOKEN or MAIL_TO")

def send_email(records, subject=None, body=None):
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD"]
    missing = [x for x in required if not os.getenv(x)]
    if missing:
        raise RuntimeError("Missing secrets: " + ", ".join(missing))

    recipients = get_recipients()
    if not recipients:
        raise RuntimeError("Subscriber list is empty")

    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject or f"IPK Monitor — нові ІПК за {date.today().strftime('%d.%m.%Y')}"
    msg.set_content(body if body is not None else digest_text(records))
    msg.add_alternative(digest_html(records), subtype="html")

    port = int(os.environ["SMTP_PORT"])
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], port, context=context, timeout=30) as server:
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(os.environ["SMTP_HOST"], port, timeout=30) as server:
            server.starttls(context=context)
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.send_message(msg)
    print(f"[MAIL] Gmail SMTP accepted message for {len(recipients)} recipient(s).")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="ipk.sqlite3")
    p.add_argument("--max-pages", type=int, default=8)
    p.add_argument("--overlap-days", type=int, default=7)
    p.add_argument("--max-new-details", type=int, default=120)
    p.add_argument("--delay", type=float, default=0.2)
    p.add_argument("--send-email", action="store_true")
    p.add_argument("--test-email", action="store_true", help="Send a test email even when there are no new IPKs")
    p.add_argument("--check-subscribers", action="store_true", help="Validate subscriber endpoint and print active count without scraping or sending mail")
    p.add_argument("--strict-tls", action="store_true", help="Do not allow the known VOBU mirror certificate workaround")
    args = p.parse_args()
    if args.check_subscribers:
        recipients = get_recipients()
        print(f"Subscriber check OK: {len(recipients)} active recipient(s).")
        return
    scraper = Scraper(args.db, delay=args.delay, allow_insecure_mirror=not args.strict_tls)
    new_records = scraper.collect(args.max_pages, args.overlap_days, args.max_new_details)
    pending = scraper.pending_records()
    print(f"[OUTBOX] newly_collected={len(new_records)} pending_delivery={len(pending)}")
    print(digest_text(new_records))
    if args.test_email:
        send_email([], subject=f"IPK Monitor test — {date.today().strftime('%d.%m.%Y')}", body="Тестова розсилка IPK Monitor через Gmail SMTP успішна.")
    elif args.send_email and pending:
        for offset in range(0, len(pending), 50):
            batch = pending[offset:offset + 50]
            send_email(batch)
            scraper.mark_emailed(batch)
            print(f"[OUTBOX] accepted={len(batch)} remaining={len(scraper.pending_records())}")
    elif args.send_email:
        print("[MAIL] No pending consultations to send.")
    scraper.db.close()

if __name__ == "__main__":
    main()
