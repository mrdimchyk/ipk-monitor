#!/usr/bin/env python3
import argparse
import hashlib
import os
import re
import sqlite3
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from urllib.parse import urljoin, urlparse
from html import escape

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://ipk.vobu.ua/"
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
    def __init__(self, db_path="ipk.sqlite3", timeout=30, delay=0.2):
        self.db = sqlite3.connect(db_path)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; IPK-Digest/2.0)",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.7",
        })
        self.timeout = timeout

    def get(self, url):
        r = self.session.get(url, timeout=self.timeout)
        r.raise_for_status()
        r.encoding = r.apparent_encoding or r.encoding
        return r

    @staticmethod
    def clean(s):
        return re.sub(r"[ \t\r\f\v]+", " ", s or "").strip()

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

    def exists(self, url):
        return self.db.execute("SELECT 1 FROM ipk WHERE source_url=?", (url,)).fetchone() is not None

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

    def latest_date(self):
        row = self.db.execute("SELECT MAX(ipk_date) FROM ipk").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def collect(self, max_pages=1, overlap_days=7, max_new_details=10):
        latest = self.latest_date()
        cutoff = latest - timedelta(days=overlap_days) if latest else None
        found = []
        for page in range(1, max_pages + 1):
            url = BASE_URL if page == 1 else f"{BASE_URL}?page={page}"
            print(f"[LIST] {url}", file=sys.stderr)
            response = self.get(url)
            records = self.parse_listing(response.text, response.url)
            print(f"[LIST] parsed={len(records)}", file=sys.stderr)
            if not records:
                break
            for rec in records:
                if cutoff and date.fromisoformat(rec.ipk_date) < cutoff:
                    continue
                if self.exists(rec.url):
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

def send_email(records):
    required = ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "MAIL_TO"]
    missing = [x for x in required if not os.getenv(x)]
    if missing:
        raise RuntimeError("Missing secrets: " + ", ".join(missing))
    msg = EmailMessage()
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg["Subject"] = f"Нові ІПК — {date.today().strftime('%d.%m.%Y')}"
    msg.set_content(digest_text(records))
    msg.add_alternative(digest_html(records), subtype="html")
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"]), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.send_message(msg)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="ipk.sqlite3")
    p.add_argument("--max-pages", type=int, default=1)
    p.add_argument("--overlap-days", type=int, default=7)
    p.add_argument("--max-new-details", type=int, default=10)
    p.add_argument("--delay", type=float, default=0.2)
    p.add_argument("--send-email", action="store_true")
    args = p.parse_args()
    scraper = Scraper(args.db, delay=args.delay)
    new_records = scraper.collect(args.max_pages, args.overlap_days, args.max_new_details)
    print(digest_text(new_records))
    if args.send_email and new_records:
        send_email(new_records)

if __name__ == "__main__":
    main()
