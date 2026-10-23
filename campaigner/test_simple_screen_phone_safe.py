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
