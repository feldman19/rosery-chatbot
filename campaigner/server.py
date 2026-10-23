"""Loopback-only synthetic campaign-manager demonstration. Not a hosted auth service."""
import argparse
import gzip
import hmac
import json
import re
from pathlib import Path
import secrets
import socket
import threading
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from analysis import summarize
from business_context import load_rosery_dossier
from creative_preparation import CreativePreparation
from local_uploads import LocalUploads
from action_recommendations import build_recommendations
from owner_action_proposals import build_owner_action_proposals
from owner_action_results import execute_local
from outcome_tracking import OutcomeTracker, OutcomeDenied
from creative_drift import CreativeDrift
from account_history import AccountHistory, chat_context as history_chat_context
from meta_direct import read_account_history
import true_profit
from rulebook import load_rulebook
from change_history_evidence import load_saved_change_evidence
from core import Campaigner, Denied
from evidence import SnapshotStore
from workflows import TaskStore
from assistant import KnowledgeAssistant, HandoffQueue, AssistantDenied
from intelligence import portfolio_report
from pilot import pilot_report, employee_pilot
from registration import registration_report, answer_registration
from reviews import review_history
from connections import connection_status
from meta_direct import configure as configure_meta, read_reports as read_meta_reports, status as meta_status
from live import LiveReports
from decisions import context as decision_context, build_report as decision_report
from action_desk import ActionDesk
from decision_loop import DecisionLoop
from automation_service import report as automation_report
from private_orders import PrivateOrders
from flashy_direct import configure as configure_flashy, status as flashy_status
from operator_service import report as operator_report
from experiment_planner import plan_experiment
from work_products import WorkProducts
from meta_write_connection import configure as configure_meta_writer
from exact_approval_bridge import ExactApprovalBridge
# BEGIN auto-mode-shadow (read-only)
from automation_policy import AutomationPolicy
from automation_shadow import AutomationShadow
# END auto-mode-shadow
from campaign_operations import CampaignOperations
from operations_config import operation_proposal
from management_report import build_report as management_report
from visual_workspace import build_workspace
from weekly_creative_context import weekly_creative_context
from claude_chat import ClaudeChat
from chat_rules_context import chat_rule_context
from decision_memory import DecisionMemory
from launch_workflow import LaunchWorkflow
from launch_materials import LaunchMaterials
from work_status import work_status
from work_journal import WorkJournal
from work_intake import WorkIntake
from launch_proposal import LaunchProposal
from preparation_loop import PreparationLoop,digest as preparation_digest
from preparation_task import PreparationTask,PreparationActionError
from drive_oauth import DriveOAuth
from drive_direct import status as drive_status
from strategy_desk import StrategyDesk
from urllib.parse import urlsplit,parse_qs,unquote_to_bytes
from shopify_direct import configure as configure_shopify, read_reports as read_shopify_reports, status as shopify_status, SetupError

ROOT = Path(__file__).resolve().parent
TENANT, ACTOR, ACCOUNT, ENTITY = 'demo-rosery', 'local-owner', 'demo-meta', 'demo-launch'


class ExclusiveHTTPServer(ThreadingHTTPServer):
    # Windows SO_REUSEADDR can admit a second listener on the same address.
    # Reserve the listener before starting any collector or execution worker.
    allow_reuse_address = False
    allow_reuse_port = False

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def accepts_gzip(header):
    """Explicit exclusions override a wildcard; malformed weights fail closed."""
    weights = {}
    for item in header.split(','):
        parts = [part.strip().lower() for part in item.split(';')]
        coding = parts[0]
        if coding not in ('gzip', '*'):
            continue
        weight = 1.0
        if len(parts) > 1:
            if len(parts) != 2 or not re.fullmatch(r'q=(?:0(?:\.\d{0,3})?|1(?:\.0{0,3})?)', parts[1]):
                weight = 0.0
            else:
                weight = float(parts[1][2:])
        weights[coding] = min(weights.get(coding, 1.0), weight)
    return weights.get('gzip', weights.get('*', 0)) > 0


def make_server(port=8766, data_dir=None, workspace='employee', start_live=False, start_execution_workers=True):
    if workspace not in ('employee', 'manager'):
        raise ValueError('Invalid local workspace')
    data = Path(data_dir or ROOT / '.local')
    data.mkdir(parents=True, exist_ok=True)
    live=LiveReports(data)
    claude_chat=ClaudeChat(data)
    decision_memory=DecisionMemory(data / "decision-memory.sqlite3")
    decision_memory.bind("rosery", "393059510164294")
    outcomes=OutcomeTracker(data/'outcome-tracking.sqlite3',verified_changes=lambda:owner_actions.verified_changes())
    drift=CreativeDrift(data/'creative-drift.sqlite3')
    history=AccountHistory(data,reader=read_account_history if start_live and workspace=='manager' else None)
    profit_settings=true_profit.ProfitSettings(data/'profit-settings.json')
    def profit_summary(snapshot=None,report=None):
        # --- true profit (Claude) ---
        snapshot=snapshot or live.snapshot();report=report or management_report(snapshot)
        seven=(report.get('windows') or {}).get('7') or {}
        try:seven_last=date.fromisoformat(seven.get('until'))
        except (TypeError,ValueError):seven_last=None
        spend={'7':((seven.get('metrics') or {}).get('spend'),seven_last),'90':history.spend('90')}
        return true_profit.summary(((snapshot.get('sources') or {}).get('shopify') or {}).get('data'),spend,profit_settings.load())
    try:
        # Verified launch readbacks are the starting state for ads not read live yet.
        drift.import_readbacks([(p.name,json.loads(p.read_text(encoding='utf-8')))
                                for p in sorted((ROOT.parent/'outputs').glob('*-readback-*.json'))])
    except (OSError,ValueError):pass
    key_file = data / 'signing.key'
    try:
        with key_file.open('xb') as key:
            key.write(secrets.token_bytes(32))
    except FileExistsError:
        pass
    engine = Campaigner(str(data / 'campaigner.sqlite3'), key_file.read_bytes())
    local_uploads = LocalUploads(data, key_file.read_bytes())
    # --- simple-screen creative launch (Claude): uploads → analysis → copy → strategy; no Meta writes ---
    from creative_launch import CreativeLaunch
    from creative_vision import CreativeVision
    import copy_variants, launch_strategy
    creative_launch=CreativeLaunch(data,uploads=local_uploads,
        analyze=lambda assets,owner_brief:CreativeVision(data).analyze(assets,owner_brief=owner_brief,local_image_review_consent=True),
        copy=copy_variants.variants,plan=launch_strategy.plan,report=lambda:management_report(live.snapshot()),rulebook=load_rulebook)
    # --- "אשר ובצע" (Claude): owner-approved pause / +20% budget / Advantage+ off; preview → confirm → readback ---
    from owner_actions import OwnerActions,ActionBlocked
    def owner_action_report():
        # Same recommendations the screen shows (rulebook proposals live there).
        snapshot=live.snapshot();report=management_report(snapshot)
        try:report['recommendations']=build_recommendations(report,load_rosery_dossier(include_owner_financials=True))
        except ValueError:report['recommendations']=build_recommendations(report,None)
        try:report['creative_drift']=drift.check(((snapshot.get('sources') or {}).get('meta') or {}).get('data'),load_rulebook())
        except Exception:report['creative_drift']={'status':'unavailable','items':[]}
        return report
    owner_actions=OwnerActions(data,report=owner_action_report)
    # --- learning (Claude): Ad Score in shadow beside the rules + hypothesis journal with an evidence gate ---
    from score_shadow import ScoreShadow
    from hypotheses import Hypotheses
    score_shadow=ScoreShadow(data/'score-shadow.sqlite3');hypotheses=Hypotheses(data/'hypotheses.json')
    def learn(report):
        score_shadow.record(report)
        result=score_shadow.summary(datetime.now(timezone.utc).date())
        hypotheses.update_evidence('ad_score_beats_3day_roas',result['score_right'],result['rules_right'],result['not_judgeable'],result['judged'][-20:])
        v=result['variant'];hypotheses.update_evidence('ctr_weight_not_needed',v['variant_right'],v['main_right'],v['not_judgeable'],v['judged'][-20:])
        return result
    launch_media_cache={}
    def launch_media(key):
        if not re.fullmatch(r'local_[a-f0-9]{32}~[a-f0-9]{64}',key):raise ValueError('Invalid media key')
        identity,sha=key.split('~');asset=local_uploads.resolve([{'id':identity,'sha256':sha}])[0]
        path=data/'drive-assets'/(identity+'-'+sha+'.bin')
        if asset.get('media_kind')!='video':return path.read_bytes(),asset['mime_type']
        if key not in launch_media_cache:
            from video_validation import frames
            launch_media_cache[key]=frames(path,asset['duration_ms'],count=1)[0][1]
        return launch_media_cache[key],'image/jpeg'
    engine.bind_account(TENANT, ACCOUNT)
    try:
        engine.get_entity(TENANT, ACCOUNT, ENTITY)
    except Denied as exc:
        if str(exc) != 'unknown_entity':
            raise
        engine.seed_entity(TENANT, ACCOUNT, ENTITY, {
            'currency': 'ILS', 'budget_type': 'daily', 'budget_minor': 6000,
            'status': 'PAUSED'})
    session = secrets.token_urlsafe(32)
    evidence = SnapshotStore(str(data / 'evidence.sqlite3'))
    tasks = TaskStore(data / 'tasks.sqlite3')
    action_desk=ActionDesk(data)
    decision_loop=DecisionLoop(data)
    private_orders=PrivateOrders(data)
    prepared_work=WorkProducts(data,key_file.read_bytes())
    execution_bridge=ExactApprovalBridge(data,key_file.read_bytes())
    # BEGIN auto-mode-shadow (local policy, no write credential loading)
    automation_policy=AutomationPolicy(data,lambda:execution_bridge._control()['generation'],load_rulebook)
    def automation_snapshot():
        snapshot=live.snapshot()
        return ((snapshot.get('sources') or {}).get('meta') or {}).get('data'),management_report(snapshot)
    automation_shadow=AutomationShadow(automation_policy,automation_snapshot,outcomes,load_rulebook)
    # END auto-mode-shadow
    launch_workflow=LaunchWorkflow(data,history={'records':[
        {'kind':'business_intent','text':'פתיחת האתר לקולקציה החדשה, המשך קמפיין המכירות ותכנים חדשים מקישורי Drive','source':'user-current-chat-2026-10-17'},
        {'kind':'previous_shutdown','text':'ב־15.10 התבקשה השהיית Meta, Google ו־TikTok לסגירת האתר. נדרש מצב חי; אין הפעלה מחדש לפי תאריך בלבד','source':'outputs/rosery-media-operations-guide-v1-2026-10-15.md'},
        {'kind':'separate_goal','text':'קמפיין AFTER HOUR הוא הרשמות לאתר. התקציב שאושר לו אינו אישור להשקת קמפיין מכירות','source':'outputs/rosery-media-operations-guide-v1-2026-10-15.md'},
        {'kind':'asset_handling','text':'תמונות שנעשה בהן שימוש בפועל מועברות לתיקיית שומש, תוך שמירת הורים לא קשורים ותיעוד. בחירה בתמונה אינה שימוש','source':'user-current-chat-and-operating-guide'}]})
    launch_materials=LaunchMaterials(data,key_file.read_bytes())
    work_journal=WorkJournal(data)
    work_intake=WorkIntake(data)
    launch_proposal=LaunchProposal(data,key_file.read_bytes())
    campaign_operations=CampaignOperations(
        data,key_file.read_bytes(),
        # A proposal the owner finalized in the simple screen's upload tab comes first.
        proposal_loader=lambda: operation_proposal(creative_launch.operation_report() or launch_proposal.report(work_intake.latest())))
    preparation_loop=PreparationLoop(data,key_file.read_bytes())
    strategy_desk=StrategyDesk(data,dossier=load_rosery_dossier(),
        dossier_loader=(lambda:load_rosery_dossier(include_owner_financials=True)) if workspace=='manager' else None)
    if strategy_desk.latest() is None:
        previous_launch=launch_workflow.report(live.snapshot())['request']
        if previous_launch:
            strategy_desk.create({'goal':previous_launch['brief']})
    creative_preparation=CreativePreparation(data,intake=work_intake,workflow=launch_workflow,materials=launch_materials,proposal=launch_proposal)
    preparation_task=PreparationTask(data,intake=work_intake,workflow=launch_workflow,materials=launch_materials,proposal=launch_proposal,loop=preparation_loop,strategy=strategy_desk,creative=creative_preparation)
    def launch_report():
        initial=launch_workflow.report(live.snapshot())
        return launch_workflow.report(live.snapshot(),materials=launch_materials.status(initial['request']))
    def work_report():
        launch=launch_report()
        strategy=strategy_desk.report(live.snapshot())
        canonical=work_intake.latest()
        if canonical:strategy={**strategy,'request':canonical}
        material=launch_materials.status(launch['request'])
        # Other objectives do not inherit a historical sales plan or its materials.
        selected=(strategy.get('request') or {}).get('category')
        associated=selected not in ('awareness','new_audience','registration') and launch.get('request') and (not canonical or (launch['request']['brief']==canonical['goal'] and launch['request']['drive_urls']==canonical['drive_urls']))
        status=work_status(launch if associated else None,strategy,material if associated else None)
        job=(material.get('job') or {}) if associated else {}
        status['collection_job']=job
        if canonical:
            status['request']=canonical
            status['goal_binding']={'source':'work','request_id':canonical['id'],'revision':canonical['revision']}
        status['materials']=launch.get('materials',{}) if associated else {}
        if associated and job.get('request_id')==launch['request']['id'] and job.get('request_revision')==launch['request']['revision']:
            labels={'queued':'הבקשה בתור; הקריאה תתחיל אחרי הבקשה הקודמת','running':'מתבצעת קריאת חומרי המקור','interrupted':'קריאת החומרים הופסקה עם סגירת המערכת','failed':'קריאת החומרים נכשלה; התוכנית עדיין אינה מוכנה'}
            if job.get('state') in labels:
                status['state']='collecting' if job['state']=='running' else job['state']
                status['current_state']=labels[job['state']]
                status['next_output']='תוצאות קריאת החומרים, ולאחריהן בדיקת התאמה נפרדת'
        if canonical and canonical.get('preparation_state')=='failed':
            status.update(state='preparation_failed',current_state='הבקשה והקישורים נשמרו; הכנת העבודה לא התחילה',next_output='תיקון כשל ההכנה לפני המשך העבודה',owner_action=None)
            status['blocked'].insert(0,{'id':'preparation','title':'הכנת הבקשה נכשלה','detail':'השמירה הושלמה. דיווח מבקשה קודמת אינו התקדמות של הבקשה הזאת','owner':'system'})
        proposal=launch_proposal.report(canonical) if canonical else {}
        if associated and proposal.get('available') and status.get('state') not in ('collecting','queued','preparation_failed'):
            prepared=proposal['proposal']
            replaced={'creative_review','decision_output'}
            status['blocked']=[row for row in status['blocked'] if row.get('id') not in replaced]
            status['stages']=[row for row in status['stages'] if row.get('id') not in replaced]
            item={'id':'reviewed_proposal','title':'הצעת ההשקה הוכנה','state':'completed','detail':str(len(prepared['ads']))+' מודעות נבחרו ונבדקו חזותית, עם פיד, סטורי ואפשרויות קופי. יתר החומרים לא אושרו לפרסום','observed_at':prepared['prepared_at']}
            status['completed'].append(item);status['stages'].append(item)
            status.update(state='draft_for_review',current_state='הצעת ההשקה מוכנה לעיון בהמשך המסך',next_output='אימות דף המכירה והמדידה, בדיקת מיקומים ואישור מדויק לפני יצירת מודעות והפעלה',owner_action=None)
        status['preparation_loop']=preparation_loop.report()
        loop_job=status['preparation_loop'].get('job') or {}
        loop_labels={'queued':'ההצעה בתור להכנה','preparing':'מתבצעת הכנת הצעת ההשקה','awaiting_review':'הוכנה חבילה לביקורת עצמאית','reviewing':'הגרסה המדויקת נמסרה לביקורת עצמאית','repairing':'מתבצע תיקון מקומי לפי הביקורת','reviewed_with_gaps':'הביקורת הושלמה; נשארו פערי מוכנות','reviewed':'בדיקת ההצעה הושלמה; אין אישור לפרסום','blocked':'לופ ההכנה נעצר עם חסם מפורש','stale':'הגרסה השתנתה והביקורת הקודמת אינה תקפה'}
        if loop_job.get('state') in loop_labels:
            status['current_state']=loop_labels[loop_job['state']]
            status['state']='preparation_'+loop_job['state']
            if loop_job['state'] in ('awaiting_review','reviewing'):status['next_output']='קליטת הביקורת, תיקון ממצאים ובדיקה חוזרת של הגרסה המתוקנת'
            if loop_job['state'] in ('reviewed','reviewed_with_gaps') and proposal.get('available') and preparation_digest(proposal['proposal'])==loop_job.get('artifact_hash'):
                current_proposal=proposal['proposal']
                status['completed'].append({'id':'launch_copy_review','state':'completed','title':'הקופי תוקן ועבר ביקורת לגרסה הנוכחית','proposal_id':current_proposal.get('id'),'proposal_prepared_at':current_proposal.get('prepared_at'),'request_id':current_proposal.get('request_id'),'request_revision':current_proposal.get('request_revision')})
        status['activity']=work_journal.observe(status)
        return status
    def analyze_cycle(snapshot):
        if workspace=='manager':
            shadow_report=management_report(snapshot,change_history=load_saved_change_evidence(ROOT.parent))
            shadow_dossier=load_rosery_dossier(include_owner_financials=True)
            frozen=decision_loop.freeze_recommendations(snapshot,build_recommendations(shadow_report,shadow_dossier),shadow_dossier)
            if frozen.get('status') not in ('recorded','already_recorded'):
                raise ValueError('Shadow decision was not frozen; downstream analysis stopped')
        settings=decision_context(data)
        action_desk.sync(snapshot,settings)
        decision_loop.record(snapshot,settings)
        private_orders.refresh()
        brief=plan_experiment(snapshot)
        if brief.get('inventory'):prepared_work.save('experiment',brief['version'],brief)
        for case in operator_report(snapshot)['cases']:
            if case['type']=='tracking_fix':prepared_work.save('tracking',case['fingerprint'],case)
    live.on_cycle=analyze_cycle
    helper = KnowledgeAssistant()
    handoffs = HandoffQueue(str(data / 'questions.sqlite3'))

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *args):
            pass

        def reply(self, status, value, content_type='application/json; charset=utf-8'):
            body = value if isinstance(value,bytes) else (json.dumps(value, ensure_ascii=False) if content_type.startswith('application/json') else value).encode()
            compressible = content_type.split(';', 1)[0].strip().lower() in ('application/json', 'text/html')
            compressed = compressible and len(body) > 1024 and accepts_gzip(','.join(self.headers.get_all('Accept-Encoding', [])))
            if compressed:
                body = gzip.compress(body, compresslevel=5, mtime=0)
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            if compressible:self.send_header('Vary', 'Accept-Encoding')
            if compressed:self.send_header('Content-Encoding', 'gzip')
            if content_type.startswith('text/html') and workspace=='manager' and self.path.split('?',1)[0]=='/':
                self.send_header('Set-Cookie','campaigner_media='+session+'; HttpOnly; SameSite=Strict; Path=/api/launch/proposal/media/')
                self.send_header('Set-Cookie','campaigner_preparation_media='+session+'; HttpOnly; SameSite=Strict; Path=/api/preparation-task/media/')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' https://*.fbcdn.net https://*.cdninstagram.com; frame-ancestors 'none'; connect-src 'self'")
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def host_ok(self):
            return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': 'Invalid host'})
            if self.path.startswith(('/api/launch/proposal/media/','/api/preparation-task/media/')):
                from http.cookies import SimpleCookie
                cookie=SimpleCookie()
                try:cookie.load(self.headers.get('Cookie',''))
                except Exception:return self.reply(403,{'error':'Invalid media session'})
                candidate=self.path.startswith('/api/preparation-task/media/')
                supplied=cookie.get('campaigner_preparation_media' if candidate else 'campaigner_media')
                if workspace!='manager' or not supplied or not hmac.compare_digest(supplied.value,session) or self.headers.get('Sec-Fetch-Site','same-origin') not in ('same-origin','none'):
                    return self.reply(403,{'error':'Invalid media session'})
                try:
                    if candidate:
                        render=preparation_task.thumbnail if self.path.startswith('/api/preparation-task/media/thumb/') else preparation_task.media
                        raw,mime=render(self.path.rsplit('/',1)[-1])
                    else:raw,mime=launch_proposal.media(self.path.rsplit('/',1)[-1],work_intake.latest())
                    return self.reply(200,raw,mime)
                except (ValueError,OSError):return self.reply(404,{'error':'Preview unavailable'})
            if urlsplit(self.path).path == '/api/drive/oauth/callback':
                try:
                    if len(self.path)>16000:raise ValueError()
                    query=parse_qs(urlsplit(self.path).query,keep_blank_values=True)
                    if len(query.get('state',[]))!=1 or len(query.get('code',[]))!=1 or 'error' in query:raise ValueError()
                    grant=drive_flow.complete(query['code'][0],query['state'][0])
                    if grant.get('saved') is not True or grant.get('verified') is not True:raise ValueError()
                except Exception:
                    return self.reply(400,{'error':'אישור הדרייב לא אומת. יש להתחיל התחברות חדשה במערכת; לא נשמר חיבור חדש מאומת.'})
                # Credential commit is independent from optional downstream work.
                kickoff='בדיקת החומרים תופיע בנפרד במערכת.'
                try:
                    current=launch_report()['request']
                    if current:launch_materials.refresh(current)
                except Exception:
                    kickoff='החיבור נשמר. הפעלת קריאת החומרים לא הושלמה; ניתן לנסות אותה מהמערכת בלי להתחבר מחדש.'
                return self.reply(200,'<!doctype html><html lang="he" dir="rtl"><meta charset="utf-8"><title>ROSERY — Drive connected</title><h1>החיבור לדרייב אומת ונשמר מוצפן</h1><p>קריאה בלבד. לא פורסמו מודעות ולא שונו קבצים בדרייב.</p><p>'+kickoff+'</p><a href="/">חזרה למערכת</a></html>','text/html; charset=utf-8')
            if workspace=='manager' and self.path=='/advanced' and (data/'rosery-pilot.json').exists():
                page=(ROOT/'owner-web.html').read_text(encoding='utf-8').replace('__SESSION__',session)
                return self.reply(200,page,'text/html; charset=utf-8')
            if workspace=='manager' and self.path.split('?',1)[0]=='/':
                # Simple owner screen first; the full workspace stays at /advanced.
                page=(ROOT/'simple-web.html').read_text(encoding='utf-8').replace('__SESSION__',session)
                return self.reply(200,page,'text/html; charset=utf-8')
            if self.path.split('?',1)[0] == '/' or (self.path == '/lab' and workspace == 'manager'):
                template = 'manager.html' if workspace == 'manager' else 'dashboard.html'
                if workspace == 'manager' and self.path == '/' and (data / 'rosery-pilot.json').exists():
                    template = 'pilot.html'
                page = (ROOT / template).read_text(encoding='utf-8').replace('__SESSION__', session)
                page = page.replace('</html>', '<script>' + (ROOT / 'pilot-format.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'registration-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'review-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'connection-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'operator-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'strategy-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'launch-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'launch-proposal-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'owner-instructions.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'preparation-loop-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'task-overview.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'work-home.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'live-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'decision-ui.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'meta-execution-panel.js').read_text(encoding='utf-8') + '</script><script>' + (ROOT / 'pilot-ui.js').read_text(encoding='utf-8') + '</script></html>')
                return self.reply(200, page, 'text/html; charset=utf-8')
            if self.path.startswith('/api/creative-launch/media/'):
                if workspace!='manager' or not hmac.compare_digest(self.headers.get('X-Local-Session',''),session):
                    return self.reply(403,{'error':'Invalid local session'})
                try:raw,mime=launch_media(self.path.rsplit('/',1)[-1])
                except (ValueError,KeyError,OSError):return self.reply(404,{'error':'Not found'})
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw);return
            if self.path not in ('/api/learning', '/api/daily-brief', '/api/approve-execute', '/api/creative-launch', '/api/automation-shadow', '/api/outcomes', '/api/profit', '/api/state', '/api/evidence', '/api/workspace', '/api/diagnostics', '/api/pilot', '/api/registration', '/api/reviews', '/api/live', '/api/decisions', '/api/action-desk', '/api/decision-loop','/api/automation','/api/operator','/api/experiment','/api/meta-execution/status','/api/launch','/api/drive/oauth/status','/api/strategy','/api/work/status','/api/launch/proposal','/api/preparation-loop','/api/preparation-task','/api/campaign-operations','/api/management-report','/api/visual-workspace','/api/claude-chat','/api/decision-memory'):
                return self.reply(404, {'error': 'Not found'})
            if not hmac.compare_digest(self.headers.get('X-Local-Session', ''), session):
                return self.reply(403, {'error': 'Invalid local session'})
            # BEGIN auto-mode-shadow
            if self.path == '/api/automation-shadow':
                if workspace!='manager':return self.reply(403,{'error':'זמין לבעלים בלבד'})
                return self.reply(200,automation_shadow.status())
            # END auto-mode-shadow
            if self.path == '/api/pilot':
                try:
                    report = pilot_report(data / 'rosery-pilot.json')
                    return self.reply(200, report if workspace == 'manager' else employee_pilot(report))
                except (ValueError, KeyError, TypeError):
                    return self.reply(503, {'error':'Pilot evidence invalid; no live action permitted'})
            if self.path == '/api/workspace':
                return self.reply(200, {'role': workspace, 'tasks': tasks.list(),
                                       'questions': handoffs.list(), 'ai_connected': False,
                                       'demo': True, 'external_notifications': False})
            if workspace != 'manager':
                return self.reply(403, {'error': 'הפעולה זמינה רק בסביבת המנהל המקומית'})
            if self.path == '/api/preparation-loop':
                return self.reply(200,preparation_loop.report())
            if self.path == '/api/reviews':
                try:
                    return self.reply(200, review_history(ROOT))
                except (ValueError, KeyError, TypeError, OSError):
                    return self.reply(503, {'error':'Review history invalid'})
            if self.path == '/api/live':
                return self.reply(200,live.snapshot())
            if self.path == '/api/meta-execution/status':
                return self.reply(200,execution_bridge.status())
            if self.path == '/api/campaign-operations':
                try:return self.reply(200,campaign_operations.status())
                except (ValueError,OSError):return self.reply(503,{'error':'לא ניתן לאמת את יומן הביצוע. לא בוצע שינוי.'})
            if self.path == '/api/decision-memory':
                try:
                    return self.reply(200,decision_memory.context('rosery','393059510164294',live.snapshot()))
                except (ValueError, OSError):
                    return self.reply(503,{'error':'לא ניתן לאמת את הראיות השמורות; לא נוצרה המלצה.'})
            if self.path == '/api/claude-chat':
                return self.reply(200,claude_chat.status())
            if self.path == '/api/visual-workspace':
                return self.reply(200,build_workspace(live.snapshot()))
            if self.path == '/api/management-report':
                try:
                    source_proposal=launch_proposal.report(work_intake.latest())
                    operations=campaign_operations.status()
                    snapshot=live.snapshot()
                    report=management_report(snapshot,proposal_report=source_proposal,operations_status=operations,change_history=load_saved_change_evidence(ROOT.parent))
                    report['weekly_creative_context']=weekly_creative_context(report,build_workspace(snapshot),snapshot=snapshot)
                    report['owner_actions']=build_owner_action_proposals(report,operations,source_proposal)
                    dossier=load_rosery_dossier(include_owner_financials=workspace=='manager')
                    try:recommendations=build_recommendations(report,dossier)
                    except ValueError:
                        # A missing/partial business profile must not hide the rulebook proposals.
                        recommendations=build_recommendations(report,None);recommendations['dossier_status']='unavailable'
                    if workspace=='manager':
                        report['business_economics']=dossier.get('economics_evidence')
                    recommendations.pop('historical_financial_context',None)
                    report['recommendations']=recommendations
                    if workspace=='manager':
                        # Tracking is local bookkeeping; its failure must not hide today's plan.
                        try:
                            outcomes.record_offers(recommendations.get('today'),report)
                            outcomes.refresh(((snapshot.get('sources') or {}).get('meta') or {}).get('data'),report)
                            report['outcomes_status']='available'
                        except Exception:
                            report['outcomes_status']='unavailable'
                        try:report['creative_drift']=drift.check(((snapshot.get('sources') or {}).get('meta') or {}).get('data'),load_rulebook())
                        except Exception:report['creative_drift']={'status':'unavailable','items':[],'unknown':[],'provider_writes':0,'live_action_allowed':False}
                    return self.reply(200,report)
                except (ValueError,KeyError,TypeError,OSError):return self.reply(503,{'error':'דוח הניהול אינו זמין כרגע. לא בוצע שינוי בקמפיינים.'})
            if self.path == '/api/learning':
                # What the system is testing, with evidence; shadow only, nothing is changed in Meta.
                if workspace!='manager':return self.reply(403,{'error':'Owner-local only'})
                try:
                    import sqlite3 as _sq
                    with _sq.connect(score_shadow.path) as db:
                        last=db.execute('SELECT ad_id,payload FROM calls WHERE day=(SELECT MAX(day) FROM calls)').fetchall()
                    scores={ad:json.loads(raw)['score'] for ad,raw in last}
                    return self.reply(200,{'hypotheses':hypotheses.list(),'shadow':score_shadow.summary(datetime.now(timezone.utc).date()),'scores':scores,'provider_writes':0})
                except Exception:return self.reply(503,{'error':'הלמידה אינה זמינה כרגע.'})
            if self.path == '/api/daily-brief':
                # Morning summary text, read-only; delivery to the phone is separate.
                if workspace!='manager':return self.reply(403,{'error':'Owner-local only'})
                from daily_brief import build as build_brief
                try:return self.reply(200,{'text':build_brief(owner_action_report(),owner_actions.status()['receipts'])})
                except Exception:return self.reply(503,{'error':'הסיכום אינו זמין כרגע.'})
            if self.path == '/api/approve-execute':
                if workspace!='manager':return self.reply(403,{'error':'Owner-local only'})
                return self.reply(200,owner_actions.status())
            if self.path == '/api/creative-launch':
                if workspace!='manager':return self.reply(403,{'error':'Owner-local only'})
                return self.reply(200,creative_launch.status())
            if self.path == '/api/profit':
                if workspace!='manager':return self.reply(403,{'error':'Owner-local only'})
                try:return self.reply(200,profit_summary())
                except Exception:return self.reply(503,{'error':'חישוב הרווחיות אינו זמין כרגע. לא בוצע שינוי בחשבון.'})
            if self.path == '/api/outcomes':
                if workspace!='manager':return self.reply(403,{'error':'Owner-local only'})
                try:
                    snapshot=live.snapshot();report=management_report(snapshot)
                    outcomes.refresh(((snapshot.get('sources') or {}).get('meta') or {}).get('data'),report)
                    return self.reply(200,outcomes.list(report))
                except Exception:return self.reply(503,{'error':'מעקב התוצאות אינו זמין כרגע. לא בוצע שינוי בקמפיינים.'})
            if self.path == '/api/launch':
                return self.reply(200,launch_report())
            if self.path == '/api/preparation-task':
                return self.reply(200,preparation_task.report())
            if self.path == '/api/work/status':
                return self.reply(200,work_report())
            if self.path == '/api/launch/proposal':
                return self.reply(200,launch_proposal.report(work_intake.latest()))
            if self.path == '/api/drive/oauth/status':
                return self.reply(200,{**drive_flow.status(),'connection':drive_status(data)})
            if self.path == '/api/strategy':
                return self.reply(200,strategy_desk.report(live.snapshot()))
            if self.path == '/api/decisions':
                return self.reply(200,decision_report(live.snapshot(),decision_context(data)))
            if self.path == '/api/automation':
                return self.reply(200,automation_report(live.snapshot(),orders=private_orders.summary()))
            if self.path == '/api/operator':
                if workspace!='manager':return self.reply(403,{'error':'Manager workspace required'})
                return self.reply(200,operator_report(live.snapshot()))
            if self.path == '/api/experiment':
                if workspace!='manager':return self.reply(403,{'error':'Manager workspace required'})
                return self.reply(200,{'current':plan_experiment(live.snapshot()),'saved':prepared_work.latest('experiment'),'live_action_allowed':False})
            if self.path == '/api/decision-loop':
                try:return self.reply(200,decision_loop.read())
                except (ValueError,KeyError,TypeError):return self.reply(503,{'error':'יומן ההערכות לא אומת'})
            if self.path == '/api/action-desk':
                try:return self.reply(200,action_desk.sync(live.snapshot(),decision_context(data)))
                except (ValueError,KeyError,TypeError,ArithmeticError):return self.reply(503,{'error':'בדיקת העבודה לא הושלמה; אין אישור לפעולה'})
            if self.path == '/api/registration':
                try:
                    return self.reply(200, registration_report(ROOT,data_dir=data))
                except (ValueError, KeyError, TypeError, OSError):
                    return self.reply(503, {'error':'Registration evidence invalid or changed; no action permitted'})
            if self.path == '/api/diagnostics':
                return self.reply(200, portfolio_report(evidence.list(TENANT)))
            if self.path == '/api/evidence':
                return self.reply(200, {'live': False, 'snapshots': evidence.list(TENANT),
                                       'note': 'Imported reports only; never current live account state'})
            fixture = json.loads((ROOT / 'demo-data.json').read_text(encoding='utf-8'))
            self.reply(200, {'mode': 'synthetic', 'entity': engine.get_entity(TENANT, ACCOUNT, ENTITY),
                             'proposals': engine.list_proposals(TENANT), 'audit': engine.audit(TENANT),
                             'analysis': summarize(fixture)})

        def do_POST(self):
            origin = f'http://127.0.0.1:{self.server.server_port}'
            if not self.host_ok() or self.headers.get('Origin') != origin or not hmac.compare_digest(self.headers.get('X-Local-Session', ''), session):
                return self.reply(403, {'error': 'Local session and same-origin request required'})
            if self.path == '/api/local-uploads':
                if workspace != 'manager':return self.reply(403, {'error':'Owner-local only'})
                if self.headers.get('Transfer-Encoding') or len(self.headers.get_all('Content-Length', [])) != 1:
                    return self.reply(400, {'error':'נדרש גודל קובץ מדויק להעלאה'})
                mime = self.headers.get('Content-Type', '')
                video = mime in ('video/mp4', 'video/quicktime')
                if mime not in ('image/jpeg', 'image/png') and not video:
                    return self.reply(415, {'error':'אפשר להעלות תמונות JPG או PNG וסרטוני MP4 או MOV'})
                try:
                    length = self.headers.get('Content-Length', '')
                    if not length.isascii() or not length.isdigit():raise ValueError('Invalid upload size')
                    size = int(length)
                    if size > (300 if video else 20) * 1024 * 1024:
                        return self.reply(413, {'error':'הסרטון גדול מ-300MB; בחרו קובץ קטן יותר' if video else 'התמונה גדולה מ־20MB; בחרו קובץ קטן יותר'})
                    if size <= 0:raise ValueError('Empty upload')
                    name = unquote_to_bytes(self.headers.get('X-File-Name', '')).decode('utf-8', errors='strict')
                    asset = local_uploads.put(self.rfile, size=size, name=name, mime=mime, upload_id=self.headers.get('X-Upload-Id', ''))
                    return self.reply(200, {'uploaded':True, 'asset':asset, 'provider_writes':0})
                except TimeoutError:
                    return self.reply(408, {'error':'העלאת התמונה לא הסתיימה בזמן; אפשר לנסות שוב'})
                except (ValueError, UnicodeError):
                    return self.reply(400, {'error':'התמונה או פרטי ההעלאה אינם תקינים; בחרו תמונת JPG או PNG ושם קובץ רגיל'})
                except Exception:
                    return self.reply(500, {'error':'שמירת התמונה נכשלה; לא נשלח דבר למטא או לניתוח'})
            if self.headers.get('Content-Type') != 'application/json':
                return self.reply(415, {'error': 'JSON required'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > 16384:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('Object required')
                # BEGIN auto-mode-shadow
                if self.path in ('/api/automation-shadow/policy','/api/automation-shadow/stop'):
                    if workspace!='manager':return self.reply(403,{'error':'זמין לבעלים בלבד'})
                    if self.path.endswith('/stop'):
                        if body!={}:raise ValueError('עצירה אינה מקבלת פרמטרים')
                        execution_bridge.stop()
                        return self.reply(200,automation_shadow.status())
                    return self.reply(200,automation_policy.save(body))
                # END auto-mode-shadow
                if self.path.startswith('/api/decision-memory/'):
                    if workspace != 'manager':return self.reply(403,{'error':'Owner-local only'})
                    if self.path == '/api/decision-memory/query':
                        if set(body)!={'question','ad_id'}:raise ValueError('Exact question/ad fields required')
                        if body['ad_id'] is not None and (not isinstance(body['ad_id'],str) or not body['ad_id'].isdigit() or len(body['ad_id'])>100):raise ValueError('Invalid ad ID')
                        return self.reply(200,decision_memory.context('rosery','393059510164294',live.snapshot(),body['question'],body['ad_id']))
                    if self.path == '/api/decision-memory/decision':
                        if set(body)!={'hypothesis','evidence_refs','action','evaluation_plan'}:raise ValueError('Exact decision fields required')
                        return self.reply(200,decision_memory.record_decision('rosery','393059510164294',**body))
                    if self.path == '/api/decision-memory/observation':
                        if set(body)!={'decision_id','evidence_refs','design','note'}:raise ValueError('Exact observation fields required')
                        return self.reply(200,decision_memory.record_observation('rosery','393059510164294',**body))
                    return self.reply(404,{'error':'Not found'})
                if self.path == '/api/tasks':
                    return self.reply(200, tasks.create(body))
                if self.path == '/api/tasks/resubmit':
                    if set(body) != {'id', 'payload', 'expected_revision'}:
                        raise ValueError('Invalid resubmission')
                    return self.reply(200, tasks.resubmit(body['id'], body['payload'], body['expected_revision']))
                if self.path == '/api/claude-chat':
                    if workspace != 'manager':return self.reply(403,{'error':'Owner-local only'})
                    if set(body) != {'question','request_id','ad_id'}:raise ValueError('Exact chat fields required')
                    view=build_workspace(live.snapshot());selected=None
                    if body['ad_id'] is not None:
                        selected=next((a for a in view.get('ads',[]) if a['id']==body['ad_id']),None)
                        if selected is None:raise ValueError('המודעה אינה בתמונת המצב. רענן את הרשימה.')
                    # Only explicit ad content and timestamps. No credentials, customer
                    # records, browser history, raw chat or private economics are sent.
                    chosen={k:selected.get(k) for k in ('id','name','campaign_name','adset_name','status','effective_status','primary_text','headline','description','text_options')} if selected else None
                    context={'business':'ROSERY, Israeli jewelry and apparel brand','account_id':view.get('account_id'),'checked_at':view.get('checked_at'),'selected_ad':chosen,'available_ads':len(view.get('ads',[])),'image_provided':False}
                    try:context['rule_assessment']=chat_rule_context(management_report(live.snapshot()),body['ad_id'])
                    except (ValueError,KeyError,TypeError,OSError):context['rule_assessment']={'status':'unavailable'}
                    if body['ad_id'] is None:
                        # Account-wide questions also get the over-time history, trimmed to fit.
                        try:context['account_history']=history_chat_context(history.summary(load_rulebook()))
                        except Exception:context['account_history']={'status':'unavailable'}
                        try:
                            profit=profit_summary()
                            context['true_profit']={'status':profit['status'],'windows':profit.get('windows'),
                                                    'top_products':(profit.get('products') or [])[:5],'limits':profit.get('limits')}
                        except Exception:context['true_profit']={'status':'unavailable'}
                        for size in (10,6,3):
                            if len(json.dumps(context,ensure_ascii=False,sort_keys=True))<=17500:break
                            assessment=context.get('rule_assessment') or {}
                            if isinstance(assessment.get('decisions'),list):
                                assessment['omitted_decisions']=assessment.get('omitted_decisions',0)+max(0,len(assessment['decisions'])-size)
                                assessment['decisions']=assessment['decisions'][:size]
                    return self.reply(200,claude_chat.submit(body['question'],body['request_id'],context))
                if self.path in ('/api/help', '/api/help/escalate'):
                    if set(body) not in ({'question'},{'question','request_id'}) or (self.path=='/api/help' and set(body)!={'question'}):
                        raise ValueError('יש להזין שאלה בלבד')
                    result = helper.answer(body['question']) if self.path == '/api/help' else handoffs.issue(body['question'],request_id=body.get('request_id'))
                    if self.path == '/api/help/escalate' and not result.get('replayed'):
                        preparation_loop.correction(result['question'],'owner_instruction:'+result['id'])
                    return self.reply(200, result)
                if workspace != 'manager':
                    return self.reply(403, {'error': 'אישור ושינוי תקציב זמינים למנהל בלבד'})
                if self.path in ('/api/approve-execute/preview','/api/approve-execute/confirm'):
                    # Writes to Meta only on confirm, which needs the single-use token the owner saw in the preview.
                    try:
                        if self.path.endswith('/preview'):
                            if set(body)!={'target'} or not isinstance(body['target'],str) or len(body['target'])>80:raise ActionBlocked('פעולה לא מוכרת')
                            return self.reply(200,owner_actions.preview(body['target']))
                        if set(body)!={'confirm_token'} or not isinstance(body['confirm_token'],str):raise ActionBlocked('חסר אישור')
                        receipt=owner_actions.confirm(body['confirm_token'])
                        if receipt['state']=='verified' and receipt['kind'] in ('pause_ad','scale_adset_budget'):
                            # Result tracking follows the change like a "ביצעתי" mark.
                            try:outcomes.mark(receipt['target'],management_report(live.snapshot()))
                            except Exception:pass
                        return self.reply(200,receipt)
                    except ActionBlocked as error:return self.reply(409,{'error':str(error)})
                    except (OSError,KeyError) as error:return self.reply(503,{'error':'הרשאת הביצוע במטא לא זמינה. לא בוצע שינוי.'})
                if self.path == '/api/learning/outside-check':
                    # Web research with links, in a process that has only WebSearch/WebFetch; context, never proof.
                    if set(body)!={'hypothesis_id'} or not isinstance(body['hypothesis_id'],str):return self.reply(400,{'error':'מזהה השערה נדרש'})
                    hypothesis=hypotheses.get(body['hypothesis_id'])
                    if not hypothesis:return self.reply(404,{'error':'השערה לא נמצאה'})
                    if (hypothesis.get('outside') or {}).get('state')=='running':return self.reply(409,{'error':'הבדיקה כבר רצה'})
                    def run_outside(h=hypothesis):
                        from outside_check import research
                        try:hypotheses.set_outside(h['id'],'done',research(h))
                        except Exception as error:hypotheses.set_outside(h['id'],'failed',detail=str(error)[:300])
                    hypotheses.set_outside(hypothesis['id'],'running')
                    threading.Thread(target=run_outside,daemon=True,name='outside-check').start()
                    return self.reply(200,{'state':'running'})
                if self.path == '/api/creative-launch/start':
                    try:return self.reply(200,creative_launch.start(body))
                    except ValueError as error:return self.reply(400,{'error':str(error)})
                if self.path == '/api/creative-launch/finalize':
                    # Owner's campaign, budgets and confirmations → exact proposal; no Meta write here.
                    try:return self.reply(200,creative_launch.finalize(body))
                    except ValueError as error:return self.reply(400,{'error':str(error)})
                if self.path == '/api/profit/settings':
                    # Owner's own cost fields, stored locally; nothing is sent anywhere.
                    try:profit_settings.save(body)
                    except ValueError as error:return self.reply(400,{'error':str(error)})
                    return self.reply(200,profit_summary())
                if self.path == '/api/creative-drift/keep':
                    # Local note that the owner keeps this state; nothing is sent to Meta.
                    if set(body)!={'ad_id'} or not isinstance(body['ad_id'],str) or not body['ad_id'].isdigit() or len(body['ad_id'])>40:
                        raise ValueError('Exact ad ID required')
                    snapshot=live.snapshot()
                    try:result=drift.keep(body['ad_id'],((snapshot.get('sources') or {}).get('meta') or {}).get('data'))
                    except ValueError as error:return self.reply(409,{'error':str(error)})
                    return self.reply(200,result)
                if self.path in ('/api/outcomes/mark','/api/outcomes/answer'):
                    # Local record of what the owner did in Meta; nothing is sent to Meta.
                    try:
                        if self.path=='/api/outcomes/mark':
                            if set(body)!={'proposal_id'} or not isinstance(body['proposal_id'],str) or len(body['proposal_id'])>120:
                                raise ValueError('Exact proposal ID required')
                            result=outcomes.mark(body['proposal_id'],management_report(live.snapshot()))
                        else:
                            if set(body)!={'action_id','answer'} or not all(isinstance(body[k],str) for k in body):
                                raise ValueError('Exact action ID and answer required')
                            result=outcomes.answer(body['action_id'],body['answer'])
                    except OutcomeDenied as error:return self.reply(409,{'error':str(error)})
                    return self.reply(200,{'action':result,'provider_writes':0})
                if self.path == '/api/owner-actions/analyze':
                    source_proposal=launch_proposal.report(work_intake.latest())
                    operations=campaign_operations.status()
                    report=management_report(live.snapshot(),proposal_report=source_proposal,operations_status=operations,change_history=load_saved_change_evidence(ROOT.parent))
                    cards=build_owner_action_proposals(report,operations,source_proposal)['proposals']
                    return self.reply(200,execute_local(data/'owner-action-results',report,cards,body))
                if self.path.startswith('/api/campaign-operations/'):
                    action=self.path.rsplit('/',1)[-1]
                    if action=='preview':
                        result=campaign_operations.preview(body)
                    elif action=='approve':
                        if set(body)!={'operation_id','digest'}:raise ValueError('Exact operation ID and digest required')
                        result=campaign_operations.approve(body['operation_id'],body['digest'])
                    elif action=='cancel':
                        if set(body)!={'operation_id','digest'}:raise ValueError('Exact operation ID and digest required')
                        result=campaign_operations.cancel(body['operation_id'],body['digest'])
                    elif action=='reconcile':
                        if set(body)!={'operation_id','digest'}:raise ValueError('Exact operation ID and digest required')
                        result=campaign_operations.reconcile(body['operation_id'],body['digest'])
                    elif action=='abandon-video':
                        if set(body)!={'operation_id','digest','step_key','owner_confirmation'} or body['owner_confirmation'] is not True:raise ValueError('Exact owner video waiver required')
                        result=campaign_operations.abandon_unknown_video(body['operation_id'],body['digest'],body['step_key'],body['owner_confirmation'])
                    elif action=='execute':
                        if set(body)!={'operation_id'}:raise ValueError('Only operation ID accepted')
                        result=campaign_operations.enqueue(body['operation_id'])
                        campaign_operations.start_worker()
                    elif action=='schedule':
                        if set(body)!={'operation_id','at'}:raise ValueError('Exact operation and timestamp required')
                        result=campaign_operations.schedule(body['operation_id'],body['at'])
                        campaign_operations.start_worker()
                    else:return self.reply(404,{'error':'Unknown campaign operation'})
                    return self.reply(200,result)
                if self.path == '/api/preparation-task':
                    return self.reply(200,preparation_task.act(body))
                if self.path == '/api/preparation-loop/start':
                    fields={'request_id','request_revision','request_hash','artifact_hash','source_hash'}
                    binding=body.get('binding')
                    if set(body)!={'binding'} or not isinstance(binding,dict) or set(binding)!=fields:raise ValueError('Exact displayed preparation binding required')
                    if not isinstance(binding['request_id'],str) or not re.fullmatch('[A-Za-z0-9_-]{1,150}',binding['request_id']) or type(binding['request_revision']) is not int or binding['request_revision']<1 or any(not isinstance(binding[k],str) or not re.fullmatch('[a-f0-9]{64}',binding[k]) for k in ('request_hash','artifact_hash','source_hash')):raise ValueError('Invalid displayed preparation binding')
                    try:job=preparation_loop.start(expected_binding=binding)
                    except (ValueError,OSError,KeyError,TypeError):raise PreparationActionError('הבקשה, ההצעה או המקורות השתנו. רענן לפני המשך.',code='stale_preparation') from None
                    try:preparation_loop.start_worker()
                    except Exception:raise PreparationActionError('מצב תור ההכנה דורש בדיקה לפני ניסיון נוסף.',status=503,code='outcome_unknown',ambiguous=True) from None
                    return self.reply(200,{'started':True,'job':job,'no_live_actions':True})
                if self.path == '/api/work/request':
                    result=preparation_task.submit_work(body)
                    try:report=work_report()
                    except Exception:report={'state':'status_unavailable','current_state':'הבקשה נשמרה; דיווח ההתקדמות אינו זמין'}
                    return self.reply(200,{**result,'work':report})
                if self.path == '/api/strategy/request':
                    strategy_desk.create(body)
                    return self.reply(200,{'saved':True,**strategy_desk.report(live.snapshot())})
                if self.path == '/api/strategy/analyze':
                    if body!={}:raise ValueError('Analysis uses authenticated host data only')
                    return self.reply(200,strategy_desk.analyze(live.snapshot()))
                if self.path == '/api/drive/oauth/begin':
                    if body!={}:raise ValueError('Drive authorization accepts no browser credentials or scope input')
                    return self.reply(200,drive_flow.begin())
                if self.path == '/api/launch/request':
                    with preparation_task.lock:
                        saved=launch_workflow.create(body)
                        if launch_materials.status(saved['request'])['configured']:
                            launch_materials.refresh(saved['request'])
                        return self.reply(200,{'saved':True,**launch_report()})
                if self.path == '/api/launch/materials/read':
                    if body!={}:raise ValueError('Material read uses saved request only')
                    request=launch_report()['request']
                    if not launch_materials.status(request)['configured']:raise ValueError('Drive read connection required')
                    return self.reply(202,{'started':launch_materials.refresh(request)})
                if self.path.startswith('/api/meta-execution/'):
                    action=self.path.rsplit('/',1)[-1]
                    if action=='preview':
                        if set(body) not in ({'action'},{'action','budget_minor'}):raise ValueError('Only manual action and optional daily budget accepted')
                        result=execution_bridge.preview_action(body['action'],body.get('budget_minor'))
                    elif action=='approve':
                        if set(body)!={'operation_id','digest'}:raise ValueError('Exact operation ID and digest required')
                        result=execution_bridge.approve(body['operation_id'],body['digest'])
                    elif action in ('execute','reconcile'):
                        if set(body)!={'operation_id'}:raise ValueError('Only operation ID accepted')
                        result=getattr(execution_bridge,action)(body['operation_id'])
                    elif action=='stop':
                        if body!={}:raise ValueError('Stop accepts no parameters')
                        result=execution_bridge.stop()
                    else:return self.reply(404,{'error':'Unknown execution route'})
                    return self.reply(200,result)
                if self.path == '/api/connections/flashy/setup':
                    if set(body)!={'api_key'}:raise ValueError('Expected Flashy key only')
                    try:return self.reply(200,configure_flashy(data,body['api_key']))
                    except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'Flashy: זהות החשבון לא אומתה; לא נשמר מפתח חדש'})
                if self.path == '/api/connections/meta-executor/setup':
                    if set(body)!={'access_token'}:raise ValueError('Expected execution token only')
                    try:return self.reply(200,configure_meta_writer(data,body['access_token']))
                    except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'אפליקציית הביצוע הנפרדת, החשבון או ההרשאות לא אומתו. לא נשמר טוקן חדש.'})
                if self.path == '/api/connections/meta/setup':
                    if set(body)!={'access_token'}:raise ValueError('Expected token only')
                    try:return self.reply(200,configure_meta(data,body['access_token']))
                    except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'Meta: האפליקציה, ההרשאות או החשבון לא אומתו; לא נשמר מפתח חדש'})
                if self.path == '/api/live/refresh':
                    if body!={}:raise ValueError('No refresh parameters accepted')
                    return self.reply(202,{'started':live.request_refresh(),'refreshing':live.snapshot()['refreshing']})
                if self.path == '/api/decisions/context':
                    return self.reply(200,decision_context(data,body))
                if self.path == '/api/action-desk/review':
                    return self.reply(200,action_desk.update(body))
                if self.path == '/api/connections/meta/report':
                    if body!={}:raise ValueError('No report parameters accepted')
                    try:return self.reply(200,read_meta_reports(data))
                    except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'Meta: הדוח לא נקרא; בדוק הרשאה או תוקף טוקן'})
                if self.path == '/api/connections/shopify/setup':
                    if set(body)!={'client_id','client_secret'}:raise ValueError('Expected app credentials only')
                    try:return self.reply(200,configure_shopify(data,body['client_id'],body['client_secret']))
                    except SetupError as e:return self.reply(502,{'error':'Shopify: '+str(e)})
                    except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'חיבור Shopify לא אומת; לא נשמר מפתח חדש'})
                if self.path == '/api/connections/shopify/report':
                    if body!={}:raise ValueError('No report parameters accepted')
                    try:return self.reply(200,read_shopify_reports(data))
                    except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'Shopify לא החזיר דוח מאומת; נדרשת בדיקת הרשאה או חיבור'})
                if self.path == '/api/connections/check':
                    if body != {}:raise ValueError('No credential or destination input accepted')
                    if (data/'shopify-client.dpapi').exists():
                        try:
                            result=shopify_status(data);result['meta']=meta_status(data)
                            try:result['flashy']=flashy_status(data)
                            except (ValueError,OSError):result['flashy']={'configured':True,'verified':False,'registration_source_connected':False}
                            return self.reply(200,result)
                        except (ValueError,KeyError,TypeError,OSError):return self.reply(502,{'error':'Saved Shopify connection could not be verified'})
                    result=connection_status();result['meta']=meta_status(data)
                    return self.reply(200,result)
                if self.path == '/api/registration/answer':
                    if set(body) != {'revision','choice'}:raise ValueError('Only revision and choice accepted')
                    return self.reply(200,answer_registration(ROOT,data,body['revision'],body['choice']))
                if self.path == '/api/help/resolve':
                    if set(body) != {'id', 'resolution'}:
                        raise ValueError('Invalid question resolution')
                    return self.reply(200, handoffs.resolve(body['id'], body['resolution']))
                if self.path == '/api/tasks/review':
                    if set(body) != {'id', 'status', 'note', 'expected_revision'}:
                        raise ValueError('Invalid task review')
                    return self.reply(200, tasks.review(body['id'], body['status'], body['note'], body['expected_revision']))
                if self.path == '/api/propose':
                    if set(body) != {'budget_minor'}:
                        raise ValueError('Only budget_minor accepted')
                    budget = body['budget_minor']
                    if type(budget) is not int or not 0 <= budget <= 20000:
                        raise ValueError('Demo budget must be 0–20000 minor units')
                    result = engine.propose(TENANT, ACTOR, ACCOUNT, ENTITY, {'budget_minor': budget})
                elif self.path in ('/api/approve', '/api/execute', '/api/reconcile'):
                    if set(body) != {'id'} or not isinstance(body['id'], str):
                        raise ValueError('Only proposal id accepted')
                    if self.path == '/api/approve':
                        result = engine.approve(TENANT, ACTOR, body['id'])
                    elif self.path == '/api/execute':
                        result = engine.execute(TENANT, body['id'])
                    else:
                        result = engine.reconcile(TENANT, body['id'])
                else:
                    return self.reply(404, {'error': 'Not found'})
                self.reply(200, result)
            except AssistantDenied as exc:
                errors = {'unknown_issue':'השאלה לא נמצאה', 'already_resolved':'השאלה כבר נענתה',
                          'invalid_issue_id':'מזהה השאלה אינו תקין'}
                self.reply(400, {'error': errors.get(str(exc), 'נסחו שאלה קצרה בלי פרטי קשר, סיסמאות או מידע אישי')})
            except PreparationActionError as exc:
                self.reply(exc.status,{'error':str(exc),'code':exc.code,'ambiguous':exc.ambiguous})
            except (ValueError, Denied, KeyError, TypeError) as exc:
                self.reply(400, {'error': str(exc)})
            except Exception:
                self.reply(500, {'error': 'Local operation failed; inspect server, do not retry blindly'})

    class LocalServer(ExclusiveHTTPServer):
        def server_close(self):
            try:
                automation_shadow.stop()  # auto-mode-shadow
                live.stop()
            finally:
                try:
                    preparation_loop.stop()
                finally:
                    try:
                        campaign_operations.stop()
                    finally:
                        super().server_close()
    local_server=LocalServer(('127.0.0.1', port), Handler)
    try:
        drive_flow=DriveOAuth(data,local_server.server_port)
        if start_live and workspace=='manager':
            live.start()
            automation_shadow.start()  # auto-mode-shadow: independent of execution workers
            def history_loop():
                # Read-only account history, at most once a day.
                while not live.stop_event.is_set():
                    # Never alongside the 5-minute report refresh: two long Meta reads at once time out.
                    for _ in range(60):
                        if not live.running:break
                        live.stop_event.wait(5)
                    history.refresh_if_stale()
                    live.stop_event.wait(3600)
            threading.Thread(target=history_loop,daemon=True,name='rosery-readonly-history').start()
            def brief_loop():
                # Morning-summary text for the scheduled phone notification; local file, read-only toward Meta.
                from daily_brief import build as build_brief
                while not live.stop_event.wait(60):
                    try:
                        report=owner_action_report()
                        try:learn(report)
                        except Exception:pass
                        text=build_brief(report,owner_actions.status()['receipts'])
                        pending=data/'daily-brief.pending';pending.write_text(text,encoding='utf-8');pending.replace(data/'daily-brief.txt')
                    except Exception:pass
                    live.stop_event.wait(1800)
            threading.Thread(target=brief_loop,daemon=True,name='rosery-daily-brief').start()
            if start_execution_workers:
                preparation_loop.start_worker()
                campaign_operations.start_worker()
    except BaseException:
        local_server.server_close()
        raise
    return local_server


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--workspace', choices=('employee', 'manager'), default='employee')
    parser.add_argument('--data-dir', help='Existing data folder (for example the original install\'s campaigner\\.local)')
    parser.add_argument('--no-startup-execution-workers', action='store_true', help='Keep read-only report refresh; execution starts only after an explicit operator action')
    args = parser.parse_args()
    server = make_server(args.port, args.data_dir, workspace=args.workspace,start_live=True,start_execution_workers=not args.no_startup_execution_workers)
    print(f'Local {args.workspace} prototype: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
