"""Home screen promises to a non-expert owner: pick a period, see what each button does, real headings."""
import re
import unittest
from pathlib import Path

PAGE = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
TODAY = PAGE.split('<section data-tab="today"', 1)[1].split('</section>', 1)[0]


class HomeScreenTests(unittest.TestCase):
    def test_period_picker_comes_first_with_presets_and_days(self):
        self.assertLess(TODAY.index('id="period-presets"'), TODAY.index('id="hero"'))
        self.assertIn('id="period-days"', TODAY)
        for label in ("'היום'", "'אתמול'", "'3 ימים'", "'7 ימים'"):
            self.assertIn(label, PAGE)

    def test_recommendations_say_which_days_they_use(self):
        self.assertIn('ההמלצות מתבססות תמיד על 3 הימים המלאים האחרונים', TODAY)

    def test_every_action_button_is_explained(self):
        for words in ('לעצור את המודעה', 'להעלות תקציב של ', 'הסר מהרשימה', 'לחכות למחר', 'לפתוח את המודעה במטא', 'מציג את השינוי לבדיקה.', 'מסתיר את המצב הנוכחי בלי לשנות את המודעה.'):
            self.assertIn(words, PAGE)
        self.assertIn("setAttribute('aria-describedby',help.id)", PAGE)

    def test_section_titles_are_headings_and_page_titles_take_focus(self):
        self.assertNotIn('<div class="label"', PAGE)
        self.assertEqual(len(re.findall(r'<h1 id="[\w-]+" tabindex="-1"', PAGE)), 5)

    def test_the_screen_shows_which_version_it_runs(self):
        import server
        self.assertIn('__VERSION__', PAGE)
        version = server.screen_version()
        self.assertRegex(version, r'^(גרסה [0-9a-f]{7,12} · )?המסך עודכן \d\d\.\d\d \d\d:\d\d$')

    def test_unknown_is_never_shown_as_zero(self):
        self.assertIn("'לא ידוע עדיין'", PAGE)
        self.assertIn("'לא ידוע'", PAGE)


if __name__ == '__main__':
    unittest.main()
