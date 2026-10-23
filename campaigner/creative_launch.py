"""Simple-screen launch preparation: uploaded creatives → analysis → 5+5 copy → strategy → draft.

Never writes to Meta. The result is a v2 draft (see launch_proposal_schema) that the owner
completes (campaign, daily budget for new ad sets, answers to rule questions) before the
executor builds an exact manifest for approval. One job at a time; a restart never replays
model work. Each video gets its first still saved as a thumbnail upload, because a video ad
needs a thumbnail image.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import re
import threading
import uuid
from pathlib import Path

STAGES = {'analyzing': 'מנתח את התכנים (תמונות, פריימים מהסרטונים ותמלול)',
          'writing_copy': 'מגדיר למי כותבים ובאיזו שפה, כותב 5 טקסטים ו-5 כותרות, ובודק שכל שורה ברורה',
          'planning': 'מחליט לפי החוקים שלך: אד-סט חדש או קיים',
          'ready_for_owner': 'ההצעה מוכנה. נשארו קמפיין, תקציב ותשובות שלך',
          'blocked': 'נעצר', 'interrupted': 'נקטע'}
DESTINATION = 'https://roseryjewelry.com/'
# Owner rule "disable_text_improvements": every Advantage+ enhancement off; relevant comments stay on (2026-10-23).
# Names are the ones Meta returned on the account's own creatives on 2026-10-23; a creative made without them
# gets Meta's defaults (standard_enhancements was on in three of four).
ENHANCEMENTS_OFF = (
    'adapt_to_placement add_text_overlay ads_with_benefits advantage_plus_creative app_highlights audio biz_ai '
    'carousel_to_video catalog_feed_tag creative_stickers cv_transformation description_automation dha_optimization '
    'dynamic_partner_content enable_ncs_testimonials enhance_cta feed_caption_optimization generate_cta hide_price '
    'ig_glados_feed ig_video_native_subtitle image_animation image_auto_crop image_background_gen '
    'image_brightness_and_contrast image_enhancement image_templates image_text_translation image_touchups image_uncrop '
    'local_store_extension media_liquidity_animated_image media_order media_type_automation multi_photo_to_video '
    'pac_recomposition pac_relaxation product_browsing product_extensions product_metadata_automation profile_card '
    'replace_media_text reveal_details_over_time show_destination_blurbs show_summary site_extensions '
    'standard_enhancements standard_enhancements_catalog text_optimizations text_translation translate_voiceover '
    'video_auto_crop video_filtering video_highlight video_highlights video_to_image video_uncrop video_voiceover '
    'wa_mm_image_filtering').split()
CREATION_RECIPE = {'adset_fields': {'destination_type': 'WEBSITE', 'bid_strategy': 'LOWEST_COST_WITHOUT_CAP'},
                   'creative_fields': {'degrees_of_freedom_spec': {'creative_features_spec': {
                       **{f: {'enroll_status': 'OPT_OUT'} for f in ENHANCEMENTS_OFF}, 'inline_comment': {'enroll_status': 'OPT_IN'}}}},
                   'optimization_type': 'DEGREES_OF_FREEDOM'}
COPY_RULES = ['full_copy_set', 'cta_shop_now', 'no_carousel', 'offer_in_headline']
URL_TAGS = 'utm_source={{site_source_name}}&utm_medium=paid_social&utm_campaign={{campaign.id}}&utm_content={{ad.id}}&adset_id={{adset.id}}'


class CreativeLaunch:
    def __init__(self, directory, *, uploads, analyze, copy, plan, report, rulebook, clock=None):
        self.path = Path(directory) / 'creative-launch.json'
        self.uploads, self.analyze, self.copy, self.plan = uploads, analyze, copy, plan
        self.report, self.rulebook = report, rulebook
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock = threading.RLock()
        self.thread = None
        state = self.status()
        if state.get('state') in ('analyzing', 'writing_copy', 'planning'):
            self._save({**state, 'state': 'interrupted', 'detail': 'ההכנה נקטעה כשהמערכת נסגרה. אפשר להתחיל שוב; דבר לא פורסם במטא.'})

    def status(self):
        # Same lock as _save: on Windows a file being read cannot be replaced.
        with self.lock:
            try:
                value = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                return {'state': 'idle', 'provider_writes': 0}
        value['stage_label'] = STAGES.get(value.get('state'))
        return value

    def _save(self, value):
        value = {**value, 'updated_at': self.clock().isoformat(), 'provider_writes': 0, 'live_action_allowed': False}
        with self.lock:
            pending = self.path.with_suffix('.pending')
            pending.write_text(json.dumps(value, ensure_ascii=False, indent=1), encoding='utf-8')
            pending.replace(self.path)
        return value

    def start(self, body):
        if not isinstance(body, dict) or set(body) != {'goal', 'sources', 'consent', 'campaign_id'}:
            raise ValueError('נדרשים מטרה, קבצים, אישור שיתוף וקמפיין')
        if body['consent'] is not True:
            raise ValueError('צריך לאשר שליחה של התמונות והפריימים לניתוח ב-Claude. הקבצים נשמרו רק במחשב.')
        from creative_vision import validate_creative_brief
        validate_creative_brief(body['goal'])
        if body['campaign_id'] is not None and (not isinstance(body['campaign_id'], str) or not re.fullmatch(r'\d{5,30}', body['campaign_id'])):
            raise ValueError('קמפיין לא תקין')
        sources = self.uploads.resolve(body['sources'])
        with self.lock:
            if self.thread and self.thread.is_alive():
                raise ValueError('הכנה אחרת עדיין רצה. חכה שתסתיים.')
            job = self._save({'state': 'analyzing', 'id': 'launch-' + uuid.uuid4().hex[:16], 'goal': body['goal'].strip(),
                              'campaign_id': body['campaign_id'], 'sources': [{'id': s['id'], 'sha256': s['sha256']} for s in sources],
                              'started_at': self.clock().isoformat()})
            self.thread = threading.Thread(target=self._run, args=(job, sources), daemon=True, name='creative-launch')
            self.thread.start()
            return self.status()

    def _run(self, job, sources):
        try:
            assets = [{**s, 'asset_id': s['id']} for s in sources]
            analysis = self.analyze(assets, owner_brief=job['goal'])
            rows = {r['asset_id']: r for r in analysis['assets']}
            video = analysis.get('video_review') or {}
            self._save({**job, 'state': 'writing_copy'})
            copies = {}
            for source in sources:
                args = dict(transcript=(video.get(source['id']) or {}).get('transcript'), source_name=source.get('name'), goal=job['goal'])
                # One fresh attempt when the copy breaks a rule (an unbacked claim, too many store-fact
                # headlines, a wrong count); a second failure blocks.
                try:
                    copies[source['id']] = self.copy(rows[source['id']], self.rulebook(), **args)
                except ValueError:
                    copies[source['id']] = self.copy(rows[source['id']], self.rulebook(), **args)
            self._save({**job, 'state': 'planning'})
            report = self.report()
            strategy = self.plan(sources, analysis['assets'], report, self.rulebook(), campaign_id=job['campaign_id'])
            draft = self._draft(job, sources, rows, video, copies, strategy)
            self._save({**job, 'state': 'ready_for_owner', 'draft': draft,
                        'audiences': {g['key']: g.get('audience') for g in strategy['adsets'] if g.get('audience')},
                        # Who the copy was written for, and what the customer-read flagged; beside the draft, not in it.
                        'copy_briefs': {k: {'brief': c.get('brief'), 'customer_read': c.get('customer_read')} for k, c in copies.items()},
                        'campaigns': self._campaigns(report), 'analysis': [self._public_row(rows[s['id']], s, video.get(s['id']))
                                                                          for s in sources]})
        except Exception as error:
            issues = getattr(error, 'issues', None)
            self._save({**job, 'state': 'blocked', 'detail': str(error)[:400], **({'claim_issues': issues} if issues else {})})

    # ---- owner completes the draft → exact proposal for the write path (Codex's campaign_operations) ----
    def finalize(self, body):
        """Owner's campaign, daily budget per new ad set and same-angle confirmations → fresh 15-minute v2 proposal.

        Nothing is sent to Meta here. The write path previews the exact manifest from this proposal and still
        needs the owner's approval of that manifest's digest.
        """
        from launch_proposal_schema import digest, validate_proposal_v2
        if not isinstance(body, dict) or set(body) != {'campaign_id', 'budgets', 'genders', 'confirm_same_angle'}:
            raise ValueError('נדרשים קמפיין, תקציב וקהל לכל אד-סט חדש, ואישור זווית לאד-סט קיים')
        state = self.status()
        draft = state.get('draft')
        if state.get('state') != 'ready_for_owner' or not draft:
            raise ValueError('אין הצעה מוכנה. הכן הצעה מחדש.')
        if body['campaign_id'] not in {c['id'] for c in state.get('campaigns') or []}:
            raise ValueError('בחר קמפיין מכירות פעיל מהרשימה')
        proposal = json.loads(json.dumps(draft))
        budgets, confirmed = body['budgets'], body['confirm_same_angle']
        if not isinstance(budgets, dict) or not isinstance(confirmed, list):
            raise ValueError('תקציבים לא תקינים')
        for group in proposal['adsets']:
            group['rationale'] = group['rationale'][:160]
            if group['target']['mode'] == 'new':
                value = budgets.get(group['key'])
                if type(value) is not int or not 100 <= value <= 200000:
                    raise ValueError(f'תקציב יומי בין ₪1 ל-₪2,000 נדרש ל"{group["label"]}"')
                group['budget_minor'] = value
                gender = (body['genders'] or {}).get(group['key']) if isinstance(body['genders'], dict) else None
                if gender not in ('men', 'women'):
                    raise ValueError(f'בחר קהל (גברים או נשים) ל"{group["label"]}"')
                group['targeting_mode'] = 'copy_' + gender
            elif group['target']['adset_id'] not in confirmed:
                raise ValueError(f'צריך לאשר שזו אותה זווית כמו באד-סט הקיים "{group["label"]}"')
        now = self.clock()
        # A single audience in this campaign gets one new ad set. Preserve the
        # owner's total daily budget; the exact merged budget is previewed before approval.
        merged, groups, keys = {}, [], {}
        for group in proposal['adsets']:
            if group['target']['mode'] != 'new':
                groups.append(group)
                continue
            gender = group['targeting_mode']
            if gender in merged:
                merged[gender]['budget_minor'] += group['budget_minor']
                keys[group['key']] = merged[gender]['key']
            else:
                merged[gender] = group
                groups.append(group)
                keys[group['key']] = group['key']
        for group in merged.values():
            members = [a for a in proposal['ads'] if keys.get(a['adset_key']) == group['key']]
            media = 'וידאו' if all(a['media_kind'] == 'video' for a in members) else 'תמונות' if all(a['media_kind'] == 'image' for a in members) else 'תכנים'
            gender = 'גברים' if group['targeting_mode'] == 'copy_men' else 'נשים'
            group['label'] = f'{media} | תכנים חדשים | {gender} | {now:%d/%m}'
            if group['budget_minor'] > 200000:
                raise ValueError('התקציב היומי המאוחד גבוה מ-₪2,000. הקטן את התקציבים.')
        for ad in proposal['ads']:
            ad['adset_key'] = keys.get(ad['adset_key'], ad['adset_key'])
        proposal['adsets'] = groups
        proposal['strategy']['campaign_id'] = body['campaign_id']
        # Bound to the rulebook as it is now, with the same digest the write path checks.
        proposal['rules'] = {'rulebook_sha256': digest(self.rulebook()),
                             'citations': sorted(set(proposal['rules']['citations']) | set(COPY_RULES))}
        proposal.update(prepared_at=now.isoformat(), expires_at=(now + timedelta(minutes=15)).isoformat(),
                        request_revision=int(draft.get('request_revision', 1)) + 1,
                        creation_recipe=CREATION_RECIPE)
        validate_proposal_v2(proposal)
        binding = {'request_id': proposal['request_id'], 'request_revision': proposal['request_revision'], 'proposal_hash': digest(proposal)}
        confirmations = [{'adset_id': a, 'same_angle': True} for a in confirmed]
        self._save({**state, 'draft': {**draft, 'request_revision': proposal['request_revision']},
                    'final': {'proposal': proposal, 'source_binding': binding, 'owner_confirmations': confirmations}})
        return {'expected_source': binding, 'owner_confirmations': confirmations, 'expires_at': proposal['expires_at']}

    def operation_report(self):
        """The finalized proposal in the shape campaign_operations loads, or None once it has expired."""
        final = self.status().get('final')
        if not final or datetime.fromisoformat(final['proposal']['expires_at']) <= self.clock():
            return None
        return {'available': True, 'proposal': final['proposal'], 'source_binding': final['source_binding']}

    @staticmethod
    def _public_row(row, source, video):
        return {'asset_id': source['id'], 'sha256': source['sha256'], 'name': source.get('name'),
                'media_kind': source.get('media_kind', 'image'), 'observed_product': row.get('observed_product'),
                'visual_description': row.get('visual_description'), 'visible_text': row.get('visible_text'),
                'angle': row.get('angle'), 'audience_hypothesis': row.get('audience_hypothesis'), 'warnings': row.get('warnings'),
                'format': row.get('format'), 'transcript': (video or {}).get('transcript'), 'audio_status': (video or {}).get('audio_status')}

    @staticmethod
    def _campaigns(report):
        window = (report.get('windows') or {}).get('7') or {}
        return [{'id': c['id'], 'name': c.get('name'), 'status': c.get('status'), 'objective': c.get('objective')}
                for c in window.get('campaigns', []) if isinstance(c, dict) and c.get('configured_active') is True
                and c.get('objective') in ('OUTCOME_SALES', 'SALES', 'PRODUCT_CATALOG_SALES')]

    def _thumbnail(self, source):
        """First still of a video, saved as its own image upload for the ad thumbnail."""
        from video_validation import frames
        path = Path(self.path.parent) / 'drive-assets' / (source['id'] + '-' + source['sha256'] + '.bin')
        raw = frames(path, source['duration_ms'], count=1)[0][1]
        upload_id = 'thumb-' + hashlib.sha256((source['id'] + source['sha256']).encode()).hexdigest()[:40]
        thumb = self.uploads.put(io.BytesIO(raw), size=len(raw), name=(source.get('name') or 'video') + '-thumb.jpg',
                                 mime='image/jpeg', upload_id=upload_id)
        return thumb['id'], thumb['sha256']

    def _draft(self, job, sources, rows, video, copies, strategy):
        now = self.clock()
        book = self.rulebook()
        ads = []
        for source in sources:
            row, facts = rows[source['id']], video.get(source['id'])
            kind = 'video' if source.get('media_kind') == 'video' else 'image'
            entry = {'asset_id': source['id'], 'sha256': source['sha256'], 'media_kind': kind,
                     'source_name': (source.get('name') or source['id'])[:160], 'source_kind': 'device',
                     'review': {'visual_checked': True, 'method': 'Claude review of stills/image; exact SHA bound',
                                'notes': (row.get('visual_description') or 'נבדק')[:4000],
                                'audio_status': (facts or {}).get('audio_status', 'not_checked') if kind == 'video' else 'not_applicable'},
                     'video': None, 'adset_key': strategy['assignment'][source['id']],
                     'primary_text_options': copies[source['id']]['primary_text_options'],
                     'headline_options': copies[source['id']]['headline_options'],
                     'description': row.get('description') or '', 'cta': 'SHOP_NOW', 'destination_url': DESTINATION,
                     'url_tags': URL_TAGS}
            if kind == 'video':
                thumb_id, thumb_sha = self._thumbnail(source)
                entry['video'] = {'duration_ms': source['duration_ms'], 'width': source['width'], 'height': source['height'],
                                  'technical_valid': True, 'thumbnail_asset_id': thumb_id, 'thumbnail_sha256': thumb_sha}
            ads.append(entry)
        adsets = [{k: g[k] for k in ('key', 'label', 'rationale', 'product', 'angle', 'targeting_mode', 'target', 'budget_minor')}
                  for g in strategy['adsets']]
        from launch_proposal_schema import digest as canonical_digest
        rulebook_sha = canonical_digest(book)  # the same digest the write path checks
        return {'schema_version': 2, 'id': job['id'], 'request_id': job['id'], 'request_revision': 1,
                'prepared_at': now.isoformat(), 'expires_at': (now + timedelta(minutes=15)).isoformat(),
                'summary': 'הצעה מהתכנים שהעלית: ניתוח, 5+5 קופי ושיוך לפי החוקים. קהל הוא השערה, לא עובדה.',
                'strategy': {'campaign_id': job['campaign_id'], 'source': 'model_advisory'},
                # Rules this draft is built to: 5+5 copy, SHOP_NOW, one flexible ad (no carousel), offers in headlines.
                'rules': {'rulebook_sha256': rulebook_sha, 'citations': sorted(set(strategy['citations'] + COPY_RULES))},
                'adsets': adsets, 'ads': ads, 'required_format': 'single_ad_flexible', 'creation_status': 'PAUSED',
                'owner_questions': strategy['owner_questions'], 'blockers': []}
