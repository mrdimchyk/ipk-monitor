from scraper import Scraper

HTML = """
<table>
<tr><th>Дата</th><th>Номер</th><th>Видавник</th><th>Рубрика</th></tr>
<tr>
<td>21.08.2026</td>
<td><a href="/view/49306-5015-IPK-99-00-24-03-03-IPK">
5015/ІПК/99-00-24-03-03 ІПК
</a></td>
<td>ДЕРЖАВНА ПОДАТКОВА СЛУЖБА УКРАЇНИ</td>
<td>- 301 Єдиний внесок</td>
</tr>
</table>
"""

def test_parse_listing():
    s = Scraper(":memory:")
    rows = s.parse_listing(HTML, "https://ipk.vobu.ua/")
    assert len(rows) == 1
    assert rows[0].source_id == "49306"
    assert rows[0].number.startswith("5015/")
    assert rows[0].ipk_date == "2026-08-21"
    assert "Єдиний внесок" in rows[0].category
