"""Isolated actual server callback integration. Network/process execution denied."""
import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from unittest.mock import patch
from contextlib import ExitStack
from copy import deepcopy

attempts=[]
def deny_external(event,args):
    if event in ('socket.connect','socket.getaddrinfo','socket.sendto','subprocess.Popen','os.system'):
        attempts.append(event);raise RuntimeError('External activity denied by audit harness')
    if event=='socket.bind' and args[1][0]!='127.0.0.1':
        attempts.append(event);raise RuntimeError('Non-loopback bind denied')
sys.addaudithook(deny_external)

import server
from live import LiveReports
from decision_loop import DecisionLoop, digest

def stamp():return datetime.now(timezone.utc).isoformat()

def main():
    root=Path(__file__).parent
    times={'preparation_started_at':stamp()}
    snapshot=json.loads((root/'.local/live-reports.json').read_text(encoding='utf-8'))
    events=[];captured=[];phase=['construction']
    original_freeze=DecisionLoop.freeze_recommendations
    fault=[None]
    def log(name,**extra):events.append({'at':stamp(),'cycle':phase[0],'event':name,**extra})
    def live_factory(directory):
        obj=LiveReports(directory,readers={name:(lambda _:None) for name in snapshot['sources']})
        obj._source=lambda name,reader:(name,deepcopy(snapshot['sources'][name]))
        captured.append(obj);return obj
    def freeze(self,*args,**kwargs):
        log('freeze_started')
        if fault[0]=='exception':raise OSError('Synthetic local freeze failure')
        if fault[0]=='not_recorded':
            log('freeze_rejected',status='not_recorded');return {'status':'not_recorded'}
        result=original_freeze(self,*args,**kwargs);log('freeze_completed',**result);return result
    def blocked_worker(*args,**kwargs):
        attempts.append('execution_worker_start');raise RuntimeError('Execution worker forbidden')
    with TemporaryDirectory(dir=root,prefix='shadow-cycle-proof-') as directory, ExitStack() as stack:
        stack.enter_context(patch.object(server,'LiveReports',side_effect=live_factory))
        stack.enter_context(patch.object(DecisionLoop,'freeze_recommendations',freeze))
        stack.enter_context(patch.object(server.ActionDesk,'sync',lambda *a,**k:log('downstream_action_desk')))
        stack.enter_context(patch.object(DecisionLoop,'record',lambda *a,**k:log('downstream_legacy_record')))
        stack.enter_context(patch.object(server.PrivateOrders,'refresh',lambda *a,**k:log('downstream_private_orders_stub')))
        stack.enter_context(patch.object(server,'plan_experiment',lambda *a,**k:(log('downstream_plan_stub') or {})))
        stack.enter_context(patch.object(server,'operator_report',lambda *a,**k:(log('downstream_operator_stub') or {'cases':[]})))
        stack.enter_context(patch.object(server.CampaignOperations,'start_worker',blocked_worker))
        stack.enter_context(patch.object(server.PreparationLoop,'start_worker',blocked_worker))
        times['preparation_finished_at']=stamp()
        srv=server.make_server(0,data_dir=directory,workspace='manager',start_live=False,start_execution_workers=False)
        try:
            for name in ('initial','repeat'):
                phase[0]=name;captured[-1].refresh(force=True)
                assert captured[-1].analysis_error is False
                log('cycle_finished',analysis_error=captured[-1].analysis_error,
                    frozen_count=len(DecisionLoop(directory).read_frozen()))
            frozen=DecisionLoop(directory).read_frozen();assert len(frozen)==1
            before=deepcopy(frozen[0])
        finally:srv.server_close()
        phase[0]='restart_construction'
        srv=server.make_server(0,data_dir=directory,workspace='manager',start_live=False,start_execution_workers=False)
        try:
            phase[0]='after_restart';captured[-1].refresh(force=True)
            assert captured[-1].analysis_error is False
            assert DecisionLoop(directory).read_frozen()==[before]
            log('cycle_finished',analysis_error=False,frozen_count=1,record_digest=digest(before))
            for mode in ('exception','not_recorded'):
                phase[0]='failure_'+mode;fault[0]=mode
                captured[-1].refresh(force=True)
                assert captured[-1].analysis_error is True
                assert not any(x['event'].startswith('downstream') for x in events if x['cycle']==phase[0])
                assert not any(x['event']=='freeze_completed' for x in events if x['cycle']==phase[0])
                assert DecisionLoop(directory).read_frozen()==[before]
                log('cycle_failed',analysis_error=True,frozen_count=1,record_digest=digest(before))
            times['execution_finished_at']=stamp()
            outcome=DecisionLoop(directory).evaluate_frozen(before['id'])
            assert outcome['status']=='pending'
            for name in ('initial','repeat','after_restart'):
                names=[x['event'] for x in events if x['cycle']==name]
                assert names.index('freeze_completed')<names.index('downstream_action_desk')
            assert not attempts
            times['verification_finished_at']=stamp()
        finally:srv.server_close()
    result={'goal':'actual_server_analysis_callback_freezes_before_downstream',
        'stage':'isolated_integration_verified','record_id':before['id'],
        'record_frozen_at':before['payload']['frozen_at'],
        'record_digest':digest(before),'source_binding':before['payload']['input_binding'],
        'decision_count':len(before['payload']['decision_output']['recommendations']),
        'events':events,'evaluation':outcome,'timing':times,
        'waiting':{'started_at':before['payload']['frozen_at'],'finished_at':None,
                   'reason':'No suitable future observation; no maturity interval invented'},
        'external_attempts':attempts,'active_server_restarted':False,
        'boundaries':['Real make_server and LiveReports.refresh/on_cycle; two isolated server instances',
          'Provider reads replaced with unchanged saved sources; no credentials copied',
          'Downstream analysis replaced by event probes; no external effects or executing workers tested',
          'Restart means fresh server instance/local-state reload in one process, not OS restart',
          'Temporary proof database removed after readback; event receipt retained',
          'Integration proof only; not decision-quality, maturity, profit or causal validation']}
    target=root.parent/'outputs/shadow-server-cycle-acceptance-2026-10-20.json'
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
