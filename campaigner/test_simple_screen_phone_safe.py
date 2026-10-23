"""The simple screen must work on the owner's iPhone, which opens it over plain http on Tailscale.

Safari does not count that as a secure page, so secure-only browser features (crypto.randomUUID and
others) are missing there. Also guards against merge leftovers: a handler pointing at an element that is
no longer on the page broke file picking on 2026-10-23.
"""
import re
import subprocess
import unittest
from pathlib import Path

PAGE = Path(__file__).with_name('simple-web.html').read_text(encoding='utf-8')
SCRIPT = '\n'.join(re.findall(r'<script>(.*?)</script>', PAGE, re.S))


class SimpleScreenPhoneSafeTests(unittest.TestCase):
    def test_chat_preview_removes_markdown_before_truncating(self):
        helper=SCRIPT.split('function previewText',1)[1].split('async function threadList',1)[0]
        script="const assert=require('assert');function previewText"+helper+r"""
assert.equal(previewText('**bold** _text_ [link](https://example.com) `code`'),'bold text link code');
assert.equal(previewText('## Heading\n~~old~~'),'Heading old');
assert.equal(previewText(null),'');
assert.equal(previewText('**'+'a'.repeat(140)+'**').length,120);
"""
        done=subprocess.run(['node','-e',script],capture_output=True,text=True)
        self.assertEqual(done.returncode,0,done.stderr)

    def test_javascript_syntax(self):
        done = subprocess.run(['node', '--check', '-'], input=SCRIPT.encode('utf-8'), capture_output=True)
        self.assertEqual(done.returncode, 0, done.stderr.decode('utf-8'))

    def test_mobile_header_and_menu(self):
        header = PAGE.split('<header>', 1)[1].split('</header>', 1)[0]
        self.assertIn('header-nowrap', header)
        self.assertNotIn('<select', header)
        self.assertIn('flex-wrap:nowrap;white-space:nowrap', PAGE)
        self.assertIn('id="menu-sheet"', PAGE)
        self.assertIn("$('menu-sheet').showModal()", SCRIPT)
        self.assertIn("$('menu-sheet').close()", SCRIPT)

    def test_card_and_button_structure(self):
        self.assertIn('card-details', SCRIPT)
        self.assertIn('return compactCard(card)', SCRIPT)
        self.assertIn('return compactCard(c)', SCRIPT)
        for style in ('.btn.primary', '.btn.secondary', '.btn.tertiary', '.btn:disabled'):
            self.assertIn(style, PAGE)
        self.assertIn('.actions>.primary{flex-basis:100%;width:100%}', PAGE)

    def test_messenger_structure_and_empty_suggestions(self):
        for part in ('thread-row', 'thread-avatar', 'thread-text', 'thread-name', 'thread-preview', 'thread-time'):
            self.assertIn(part, SCRIPT)
        self.assertIn('class="search-field"', PAGE)
        self.assertIn('class="chat-dock"', PAGE)
        self.assertLess(PAGE.index('id="chat"'), PAGE.index('id="suggest"'))
        self.assertLess(PAGE.index('id="suggest"'), PAGE.index('id="composer"'))
        self.assertIn("$('suggest').hidden=!!(st?.turns||[]).length", SCRIPT)
        self.assertIn('position:sticky;bottom:calc(88px', PAGE)

    def test_today_order_and_hebrew_integrity(self):
        self.assertLess(PAGE.index('id="todo"'), PAGE.index('id="drift"'))
        self.assertLess(PAGE.index('id="drift"'), PAGE.index('id="acted"'))
        self.assertNotIn('????', PAGE)
        self.assertIn("$('todo-label').textContent=", SCRIPT)
        self.assertIn("$('drift-label').textContent=", SCRIPT)
        self.assertIn("$('acted-label').textContent=", SCRIPT)

    def test_text_palette_contrast_in_both_themes(self):
        def luminance(color):
            channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
            return sum((v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4) * w
                       for v, w in zip(channels, (.2126, .7152, .0722)))
        light = {'ink':'#121a16','muted':'#59665f','gold':'#85652f','stop':'#b3261e','grow':'#1a6e46','watch':'#7f5500','idle':'#5c665f'}
        dark = {'ink':'#ecf1ee','muted':'#a8b4ae','gold':'#d8b77f','stop':'#ff8a7a','grow':'#6fd8a0','watch':'#f2c46b','idle':'#a8b4ae'}
        for palette, backgrounds in ((light, ('#ffffff','#f5f4ef','#faf9f6')), (dark, ('#0a0e0c','#131916','#171e1a'))):
            for name, color in palette.items():
                self.assertIn('--'+name+':'+color, PAGE)
                for background in backgrounds:
                    values = sorted((luminance(color), luminance(background)))
                    self.assertGreaterEqual((values[1]+.05)/(values[0]+.05), 4.5, (name, background))

    def test_no_secure_only_browser_features(self):
        code = '\n'.join(line for line in SCRIPT.splitlines() if not line.lstrip().startswith('//'))
        for feature in ('randomUUID', 'navigator.clipboard', 'crypto.subtle', 'serviceWorker', 'navigator.share'):
            self.assertNotIn(feature, code, feature)
        self.assertIn('crypto.getRandomValues', code)

    def test_every_element_the_script_uses_is_on_the_page(self):
        used = set(re.findall(r"\$\('([\w-]+)'\)", SCRIPT))
        self.assertEqual(sorted(used - set(re.findall(r'id="([\w-]+)"', PAGE))), [])

    def test_file_picker_has_one_handler(self):
        self.assertEqual(SCRIPT.count("$('launch-files').onchange"), 1)
        self.assertEqual(SCRIPT.count("$('launch-form').onsubmit"), 1)


if __name__ == '__main__':
    unittest.main()
