"""No-network tests for Gemini parsing, caching and delivery fallback."""
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch
from scraper import Record, digest_html, digest_text
from gemini_digest import _summarize_batch, enrich_records


def item(num, body="Звернення товариства щодо строків виникнення податкових зобов'язань з ПДВ. " * 5):
    return Record(
        url=f"https://ipk.vobu.ua/view/{num}-sample",
        source_id=str(num),
        number=f"{num}/ІПК/99-00-21-03-02",
        ipk_date="2026-11-10",
        issuer="ДПС",
        category="Податок на додану вартість",
        content=body,
    )


class FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "items": [
                {
                    "id": 0,
                    "question": "Чи виникають зобов'язання з ПДВ при авансі?",
                    "position": "ДПС застосовує правило першої події.",
                    "impact": "Перевірити дату отримання оплати.",
                }
            ]
        }, ensure_ascii=False)}]}}]}


class GeminiTests(unittest.TestCase):
    def test_structured_response_does_not_leak_key(self):
        session = Mock()
        session.post.return_value = FakeResponse()
        got = _summarize_batch([item(1)], "SENSITIVE-KEY-TEST", "gemini-3.1-flash-lite", session=session)
        self.assertIn("правило першої події", got[0]["position"])
        args, kwargs = session.post.call_args
        self.assertIn(":generateContent", args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], "SENSITIVE-KEY-TEST")
        self.assertEqual(kwargs["json"]["generationConfig"]["responseMimeType"], "application/json")

    def test_persisted_cache_avoids_second_call(self):
        db = sqlite3.connect(":memory:")
        rec = item(20)
        with patch("gemini_digest._summarize_batch", return_value={
            0: {"question": "Чи виникають зобов'язання з ПДВ при авансі?",
                "position": "Правило першої події застосовується.",
                "impact": "Оцінити дату авансу."}
        }) as generate:
            first = enrich_records([rec], db, api_key="not-real", max_calls=1)
            self.assertEqual(first["generated"], 1)
            second = enrich_records([item(20)], db, api_key="not-real", max_calls=1)
            self.assertEqual(second["cached"], 1)
            self.assertEqual(generate.call_count, 1)
        self.assertIn("Gemini", digest_html([rec]))
        self.assertIn("Практичне значення", digest_text([rec]))

    def test_fallback_without_key_is_source_not_fabricated(self):
        rec = item(30)
        db = sqlite3.connect(":memory:")
        stats = enrich_records([rec], db, api_key="", max_calls=1)
        self.assertEqual(stats["fallback"], 1)
        self.assertIn("фрагмент", digest_text([rec]))
        self.assertNotIn("Практичне значення (Gemini)", digest_text([rec]))

    def test_model_error_still_builds_source_digest(self):
        rec = item(40)
        db = sqlite3.connect(":memory:")
        with patch("gemini_digest._summarize_batch", side_effect=ValueError("Bad result")):
            stats = enrich_records([rec], db, api_key="placeholder", max_calls=1)
        self.assertEqual(stats["fallback"], 1)
        self.assertIn("ІПК №", digest_text([rec]))


if __name__ == "__main__":
    unittest.main()
