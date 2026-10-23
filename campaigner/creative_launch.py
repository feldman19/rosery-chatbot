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
          'writing_copy': 'כותב 5 טקסטים ו-5 כותרות לכל מודעה',
          'planning': 'מחליט לפי החוקים שלך: אד-סט חדש או קיים',
          'ready_for_owner': 'ההצעה מוכנה. נשארו קמפיין, תקציב ותשובות שלך',
          'blocked': 'נעצר', 'interrupted': 'נקטע'}
DESTINATION = 'https://roseryjewelry.com/'
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
                copies[source['id']] = self.copy(rows[source['id']], self.rulebook(),
                                                 transcript=(video.get(source['id']) or {}).get('transcript'),
                                                 source_name=source.get('name'))
            self._save({**job, 'state': 'planning'})
            report = self.report()
            strategy = self.plan(sources, analysis['assets'], report, self.rulebook(), campaign_id=job['campaign_id'])
            draft = self._draft(job, sources, rows, video, copies, strategy)
            self._save({**job, 'state': 'ready_for_owner', 'draft': draft,
                        'campaigns': self._campaigns(report), 'analysis': [self._public_row(rows[s['id']], s, video.get(s['id']))
                                                                          for s in sources]})
        except Exception as error:
            issues = getattr(error, 'issues', None)
            self._save({**job, 'state': 'blocked', 'detail': str(error)[:400], **({'claim_issues': issues} if issues else {})})

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
        rulebook_sha = hashlib.sha256(json.dumps(book, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        return {'schema_version': 2, 'id': job['id'], 'request_id': job['id'], 'request_revision': 1,
                'prepared_at': now.isoformat(), 'expires_at': (now + timedelta(minutes=15)).isoformat(),
                'summary': 'הצעה מהתכנים שהעלית: ניתוח, 5+5 קופי ושיוך לפי החוקים. קהל הוא השערה, לא עובדה.',
                'strategy': {'campaign_id': job['campaign_id'], 'source': 'model_advisory'},
                'rules': {'rulebook_sha256': rulebook_sha, 'citations': sorted(set(strategy['citations'] + ['full_copy_set']))},
                'adsets': adsets, 'ads': ads, 'required_format': 'single_ad_flexible', 'creation_status': 'PAUSED',
                'owner_questions': strategy['owner_questions'], 'blockers': []}
