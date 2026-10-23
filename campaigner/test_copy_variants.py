import unittest

import copy_variants
from creative_claims import CreativeClaimReviewRequired
from rulebook import load_rulebook

ROW = {'asset_id': 'local_' + '0' * 32, 'observed_product': 'צמיד בגוון זהב', 'angle': 'מתנה', 'visual_description': 'צמיד על יד',
       'visible_text': '', 'audience_hypothesis': 'גברים צעירים', 'primary_text': 'צמיד', 'headline': 'צמיד', 'description': ''}


BRIEF = {'avatar': 'גבר בן 30 שרוצה לוק מוקפד בלי להתאמץ', 'desire': 'להיראות מסודר', 'objection': 'חושש שזה נראה מוגזם',
         'customer_words': ['נקי', 'יושב טוב', 'לכל יום'], 'brand_voice': 'ישיר, שקט, בטוח', 'message': 'צמיד עדין לכל יום'}


def answer(texts=None, heads=None, brief=None):
    return {'brief': brief or BRIEF, 'primary_text_options': texts or [f'טקסט {i}' for i in range(5)],
            'headline_options': heads or [f'כותרת {i}' for i in range(5)]}


def verdicts(bad=()):
    return {'items': [{'field': f, 'number': n, 'ok': (f, n) not in bad, 'problem': 'לא ברור' if (f, n) in bad else ''}
                      for f in ('primary_text', 'headline') for n in range(1, 6)]}


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
        no_brief = {k: v for k, v in answer().items() if k != 'brief'}
        half_brief = answer(brief={**BRIEF, 'avatar': ' '})
        for bad in (answer(texts=['a'] * 5), answer(heads=['x', 'y']), {'primary_text_options': [], 'extra': 1}, no_brief, half_brief):
            with self.assertRaises(ValueError):
                copy_variants.variants(ROW, self.rules, runner=lambda _: bad)

    def test_unsupported_claim_blocks_the_draft(self):
        bad = answer(texts=['משלוח חינם לכל הארץ'] + [f'טקסט {i}' for i in range(4)])
        with self.assertRaises(CreativeClaimReviewRequired):
            copy_variants.variants(ROW, self.rules, runner=lambda _: bad)

    def test_owner_store_facts_word_for_word_are_allowed(self):
        heads = ['משלוח חינם מעל ₪350', 'כותרת 1', 'כותרת 2', 'כותרת 3', 'כותרת 4']
        seen = {}
        def runner(text):
            seen['text'] = text
            return answer(heads=heads)
        self.assertEqual(copy_variants.variants(ROW, self.rules, runner=runner)['headline_options'], heads)
        self.assertIn('משלוח חינם מעל ₪350', seen['text'])

    def test_store_facts_only_in_up_to_two_headlines(self):
        heads = ['משלוח חינם מעל ₪350', 'החזרות חינם עד 14 יום', 'משלוח עד 3 ימי עסקים', 'כותרת 3', 'כותרת 4']
        with self.assertRaises(ValueError):
            copy_variants.variants(ROW, self.rules, runner=lambda _: answer(heads=heads))
        texts = ['משלוח חינם מעל ₪350 וגם צמיד'] + [f'טקסט {i}' for i in range(4)]
        with self.assertRaises(ValueError):
            copy_variants.variants(ROW, self.rules, runner=lambda _: answer(texts=texts))

    def test_brief_first_from_goal_examples_and_creative(self):
        seen = {}
        def runner(text):
            seen['text'] = text
            return answer()
        examples = [{'text': 'הצמיד שלא מורידים', 'roas': 4.2, 'purchases': 9}]
        result = copy_variants.variants(ROW, self.rules, goal='קולקציה לגברים שקונים לעצמם', voice_examples=examples, runner=runner)
        for part in ('קולקציה לגברים שקונים לעצמם', 'הצמיד שלא מורידים', 'avatar', 'customer_words', 'brand_voice', 'make sense'):
            self.assertIn(part, seen['text'])
        self.assertEqual(result['brief']['avatar'], BRIEF['avatar'])
        self.assertEqual(result['customer_read'], {'done': False, 'rewrites': 0, 'still_flagged': []})  # injected runner, no reader

    def test_customer_read_rewrites_flagged_lines_then_reports_what_is_left(self):
        prompts, reads = [], []
        def runner(text):
            prompts.append(text)
            return answer()
        def reviewer(text):
            reads.append(text)
            return verdicts(bad={('headline', 2)})
        result = copy_variants.variants(ROW, self.rules, runner=runner, reviewer=reviewer)
        self.assertEqual((len(prompts), len(reads)), (3, 3))  # first try + two rewrites, each read by the customer
        self.assertIn('כותרת 1', prompts[1])  # the flagged line goes back with its problem
        self.assertIn('לא ברור', prompts[1])
        self.assertEqual(result['customer_read']['still_flagged'], [{'field': 'headline', 'number': 2, 'problem': 'לא ברור'}])
        self.assertIn(BRIEF['avatar'], reads[0])  # the reader judges against the brief

    def test_clean_read_stops_after_one_round(self):
        calls = []
        result = copy_variants.variants(ROW, self.rules, runner=lambda t: calls.append(t) or answer(), reviewer=lambda t: verdicts())
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['customer_read'], {'done': True, 'rewrites': 0, 'still_flagged': []})

    def test_a_malformed_read_is_reported_as_not_done(self):
        result = copy_variants.variants(ROW, self.rules, runner=lambda t: answer(), reviewer=lambda t: {'items': 'x'})
        self.assertEqual(result['customer_read']['done'], False)

    def test_review_schema_covers_all_ten_lines(self):
        s = copy_variants.review_schema()['properties']['items']
        self.assertEqual((s['minItems'], s['maxItems']), (10, 10))
        self.assertEqual(copy_variants.schema()['required'], ['brief', 'primary_text_options', 'headline_options'])

    def test_schema_is_exactly_five_each(self):
        s = copy_variants.schema()['properties']
        self.assertEqual((s['primary_text_options']['minItems'], s['headline_options']['maxItems']), (5, 5))


if __name__ == '__main__':
    unittest.main()
