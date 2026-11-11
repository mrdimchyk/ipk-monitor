"""Regression coverage for informative mail and both configured recipients."""
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from scraper import (
    Record, Scraper, describe_consultation, digest_html,
    digest_text, get_recipients,
)


def sample_ipk():
    return Record(
        url="https://ipk.vobu.ua/view/50752-sample-IPK",
        source_id="50752",
        number="6410/ІПК/99-00-24-03-03",
        ipk_date="2026-11-04",
        issuer="ДПС України",
        category="- 101 Податок на додану вартість (ПДВ)",
        title="Індивідуальна податкова консультація",
        content=(
            "Державна податкова служба України, керуючись ст. 52 Кодексу, "
            "розглянула звернення та повідомляє. "
            "Товариство у зверненні повідомило про отримання оплати "
            "від покупця за поставлені товари і запитує про момент "
            "виникнення податкових зобов'язань з ПДВ. "
            "Відповідно до статті 187 Кодексу податкові зобов'язання "
            "зазвичай виникають за правилом першої події. "
            "Таким чином, у наведеній у зверненні ситуації "
            "необхідно врахувати фактичні умови постачання. "
            "Відповідно до п. 52.2 ст. 52 Кодексу індивідуальна "
            "консультація має індивідуальний характер."
        ),
    )


class DigestTests(unittest.TestCase):
    def test_descriptions_are_in_plain_and_html_messages(self):
        rec = sample_ipk()
        question, position = describe_consultation(rec)
        self.assertIn("Товариство", question)
        self.assertNotIn("керуючись ст. 52", question)
        self.assertIn("Таким чином", position)
        txt = digest_text([rec])
        html = digest_html([rec])
        for x in (txt, html):
            self.assertIn("6410/ІПК/", x)
            self.assertIn("Товариство", x)
            self.assertIn("Таким чином", x)
            self.assertIn(rec.url, x)
            self.assertIn("ПДВ", x)

    def test_no_text_has_non_fabricated_fallback(self):
        rec = sample_ipk()
        rec.content = ""
        question, position = describe_consultation(rec)
        self.assertIn("не вдалося", question)
        self.assertEqual(position, "")

    def test_default_recipients_include_monitor_and_mail_to(self):
        with patch.dict(os.environ, {
            "MAIL_TO": "owner@example.com",
            "MONITOR_MAILBOX": "monitor@example.com",
            "SUBSCRIBERS_URL": "",
        }, clear=True):
            recipients = get_recipients()
        self.assertEqual(recipients, ["owner@example.com", "monitor@example.com"])

    def test_subscriber_duplicate_deduplication(self):
        with patch.dict(os.environ, {
            "MAIL_TO": "monitor@example.com",
            "MONITOR_MAILBOX": "monitor@example.com",
            "SUBSCRIBERS_URL": "",
        }, clear=True):
            self.assertEqual(get_recipients(), ["monitor@example.com"])

    def test_recap_is_once_and_excludes_newly_sent(self):
        scraper = Scraper(":memory:")
        old = sample_ipk()
        scraper.save(old)
        scraper.mark_emailed([old])
        cutoff = datetime.now(timezone.utc).isoformat()
        recaps = scraper.pending_descriptive_recap(hours=36, emailed_before=cutoff)
        self.assertEqual(len(recaps), 1)
        scraper.mark_descriptive_recap(recaps)
        self.assertEqual(scraper.pending_descriptive_recap(hours=36, emailed_before=cutoff), [])
        scraper.finish_descriptive_recap()
        self.assertTrue(scraper.recap_completed())
        new = sample_ipk()
        new.url = "https://ipk.vobu.ua/view/new-2"
        new.source_id = "new-2"
        new.number = "6411/ІПК/99-00-24-03-03"
        scraper.save(new)
        scraper.mark_emailed([new])
        self.assertEqual(scraper.pending_descriptive_recap(hours=36, emailed_before=cutoff), [])


if __name__ == "__main__":
    unittest.main()
