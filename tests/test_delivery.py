"""Offline regression tests for reliable IPK collection and email outbox."""
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

from scraper import Record, SCHEMA, Scraper


class IPKReliabilityTests(unittest.TestCase):
    def make_rec(self, number="1234"):
        return Record(
            url=f"https://peredplata.vobu.ua/view/60001-{number}-IPK",
            source_id="60001" if number == "1234" else "60002",
            number=f"{number}/ІПК/99-00-21-03-02 ІПК",
            ipk_date="2026-10-03",
            issuer="ДПС України",
            category="ПДВ",
            title="Test",
            content="Consultation body",
        )

    def test_unsent_stays_pending_until_smtp_accepted(self):
        scraper = Scraper(":memory:")
        record = self.make_rec()
        scraper.save(record)
        self.assertEqual(len(scraper.pending_records()), 1)
        # An SMTP failure must not call mark_emailed.
        self.assertEqual(len(scraper.pending_records()), 1)
        scraper.mark_emailed(scraper.pending_records())
        self.assertEqual(scraper.pending_records(), [])
        scraper.mark_emailed([record])
        self.assertEqual(scraper.pending_records(), [])

    def test_existing_archive_is_migrated_without_mass_resend(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "ipk.sqlite3")
            db = sqlite3.connect(path)
            db.executescript(SCHEMA)
            old = self.make_rec()
            db.execute(
                """INSERT INTO ipk
                   (source_url, source_id, number, ipk_date, issuer,
                    category, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (old.url, old.source_id, old.number, old.ipk_date,
                 old.issuer, old.category, "2026-10-03", "2026-10-03"),
            )
            db.commit()
            db.close()
            scraper = Scraper(path)
            self.assertEqual(scraper.pending_records(), [])
            status = scraper.db.execute(
                "SELECT emailed_at FROM ipk WHERE source_url=?",
                (old.url,),
            ).fetchone()[0]
            self.assertEqual(status, "legacy-unverified")
            new = self.make_rec("1235")
            scraper.save(new)
            self.assertEqual([r.number for r in scraper.pending_records()],
                             [new.number])
            scraper.db.close()

    def test_late_published_older_dated_document_is_collected(self):
        scraper = Scraper(":memory:")
        recent = self.make_rec()
        recent.ipk_date = "2026-11-09"
        scraper.save(recent)
        scraper.mark_emailed([recent])
        listing = """<table><tr>
        <td>01.09.2026</td>
        <td><a href="/view/60002-1235-IPK">1235/ІПК/99-00-21-03-02 ІПК</a></td>
        <td>ДПС України</td><td>- 101 ПДВ</td>
        </tr></table>"""
        detail = "<html><main><h1>New backdated IPK</h1><p>Body</p></main></html>"
        scraper.get = lambda url: SimpleNamespace(
            text=detail if "/view/" in url else listing,
            url=url,
        )
        found = scraper.collect(max_pages=1, max_new_details=10)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].ipk_date, "2026-09-01")
        self.assertEqual(len(scraper.pending_records()), 1)

    def test_empty_first_listing_is_error_not_no_updates(self):
        scraper = Scraper(":memory:")
        scraper.get = lambda url: SimpleNamespace(text="<html></html>", url=url)
        with self.assertRaises(RuntimeError):
            scraper.collect(max_pages=1)

    def test_source_connect_timeout_is_retried(self):
        scraper = Scraper(":memory:", allow_insecure_mirror=False)
        response = Mock()
        response.apparent_encoding = "utf-8"
        response.encoding = "utf-8"
        scraper.session.get = Mock(side_effect=[
            requests.exceptions.ConnectTimeout("temporary outage"), response
        ])
        with patch("scraper.time.sleep"):
            scraper.get("https://ipk.vobu.ua/")
        self.assertEqual(scraper.session.get.call_count, 2)
        self.assertTrue(scraper.session.get.call_args.kwargs["verify"])


if __name__ == "__main__":
    unittest.main()
