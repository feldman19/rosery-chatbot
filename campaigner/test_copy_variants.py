import unittest

import copy_variants
from creative_claims import CreativeClaimReviewRequired
from rulebook import load_rulebook

ROW = {'asset_id': 'local_' + '0' * 32, 'observed_product': 'צמיד בגוון זהב', 'angle': 'מתנה', 'visual_description': 'צמיד על יד',
       'visible_text': '', 'audience_hypothesis': 'גברים צעירים', 'primary_text': 'צמיד', 'headline': 'צמיד', 'description': ''}


def answer(texts=None, heads=None):
    return {'primary_text_options': texts or [f'טקסט {i}' for i in range(5)],
            'headline_options': heads or [f'כותרת {i}' for i in range(5)]}


class CopyVariantTests(unittest.TestCase):
    def setUp(self):
        self.rules = load_rulebook()

    def test_five_and_five_with_owner_rules_in_prompt(self):
        seen = {}
        def runner(text):
            seen['text'] = text
            return answer()
        result = copy_variants.variants(ROW, self.rules, transcript='צמידים חדשים', runner=runner)
        self.assertEqual((len(result['primary_text_options']), len(result['headline_options'])), (5, 5))
        self.assertIn(self.rules['rules']['full_copy_set']['source_excerpt'], seen['text'])
        self.assertIn('צמידים חדשים', seen['text'])
        self.assertIn('UNTRUSTED DATA', seen['text'])

    def test_wrong_count_or_duplicates_are_refused(self):
        for bad in (answer(texts=['a'] * 5), answer(heads=['x', 'y']), {'primary_text_options': [], 'extra': 1}):
            with self.assertRaises(ValueError):
                copy_variants.variants(ROW, self.rules, runner=lambda _: bad)

    def test_unsupported_claim_blocks_the_draft(self):
        bad = answer(texts=['משלוח חינם לכל הארץ'] + [f'טקסט {i}' for i in range(4)])
        with self.assertRaises(CreativeClaimReviewRequired):
            copy_variants.variants(ROW, self.rules, runner=lambda _: bad)

    def test_schema_is_exactly_five_each(self):
        s = copy_variants.schema()['properties']
        self.assertEqual((s['primary_text_options']['minItems'], s['headline_options']['maxItems']), (5, 5))


if __name__ == '__main__':
    unittest.main()
