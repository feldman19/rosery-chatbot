"""Local pause-only scenario arithmetic. No fitted forecast or execution authority.

Benefit(pause versus continue) = spend saved - contribution forgone - change cost.
All terms refer to the SAME future horizon and affected business scope, including
budget redistribution and substitution. Past spend is evidence, not recoverable.
Bounds are supplied scenarios, never confidence intervals inferred from Meta CPA.
"""
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from business import ACTIVE as BUSINESS

ACCOUNT=BUSINESS.meta_account
MEANINGS={
 'avoidable_spend':'future_total_affected_spend_continue_minus_pause',
 'foregone_contribution':'future_net_contribution_before_ads_continue_minus_pause',
 'change_cost':'future_pause_minus_continue_change_cost_not_already_in_contribution',
}

def stamp(value):
 try:
  result=datetime.fromisoformat(value.replace('Z','+00:00'))
  return result if result.tzinfo else None
 except (AttributeError,TypeError,ValueError):return None

def number(value):
 if type(value) not in (int,float,str):raise ValueError('Numeric bound required')
 result=Decimal(str(value))
 if not result.is_finite():raise ValueError('Finite bound required')
 return result

def evaluate_pause(report,row,economics,prospective=None,*,now=None,budget_context=None):
 now=now or datetime.now(timezone.utc)
 if not now.tzinfo:raise ValueError('Aware clock required')
 if report.get('account_id')!=ACCOUNT or not row.get('id'):raise ValueError('Exact ROSERY entity required')
 if not any(x.get('id')==row['id'] and x==row for x in report.get('windows',{}).get('7',{}).get('ads',[])):
  raise ValueError('Entity must match the supplied report')
 scope={'account_id':ACCOUNT,'entity_type':'ad','entity_id':row['id']}
 if budget_context is not None and budget_context.get('scope')!=scope:
  raise ValueError('Budget evidence must match the exact pause scope')
 window=report['windows']['7'];missing=[]
 result={'version':'pause_scenario_v1','scope':scope,'entity_name':row.get('name'),
  'status':'ABSTAIN','explanation':'אין בסיס מזוהה להשוות את התוצאה העתידית של עצירה מול המשך.',
  'historical_observation':{'since':window.get('since'),'until':window.get('until'),
    'spend':row.get('spend'),'attributed_purchases':row.get('purchases'),
    'past_spend_recoverable':False,'attributed_purchases_are_incremental':False},
  'configuration':{'adset_id':row.get('adset_id'),'campaign_id':row.get('campaign_id'),
                   'configured_active':row.get('configured_active'),'effective_status':row.get('effective_status')},
  'budget_context':budget_context,
  'source_observed_at':report.get('freshness',{}).get('meta_last_success_at'),
  'economics':{'status':economics.get('status'),'missing':economics.get('missing',[])},
  'horizon':None,'benefit_pause_ils':None,'error_cost_bounds_ils':None,
  'scenario_only':True,'forecast_validated':False,'live_recommendation_allowed':False,
  'execution_allowed':False,'synthetic':bool((prospective or {}).get('synthetic',False)),
  'minimum_inputs':['future_horizon','avoidable_system_spend_after_redistribution',
                    'incremental_net_contribution_difference_including_substitution',
                    'change_restart_learning_and_tail_cost','economic_cost_basis'],
  'limitations':['No probabilities or expected loss inferred from interval bounds',
                 'Past spend may inform a forecast but is not added to avoidable future loss',
                 'Pausing one ad need not reduce campaign spend',
                 'Change costs must exclude anything already in contribution; consequences outside the horizon remain unknown unless explicitly bounded',
                 'Descriptive attribution does not identify the effect of pausing',
                 'Scenario dominance is conditional on all bounds and scope being valid']}
 source=stamp(result['source_observed_at'])
 if not source or not 0<=(now-source).total_seconds()<=900 or report.get('freshness',{}).get('state')!='fresh':
  missing.append('current_source')
 if row.get('configured_active') is not True or row.get('effective_status')!='ACTIVE':
  missing.append('currently_active_eligible_ad')
 if row.get('result_type')!='purchase':missing.append('purchase_objective')
 # Unchanged fixed costs and a desired profit reserve cancel in this relative
 # pause/continue comparison; they are not prerequisites for its arithmetic.
 missing.extend('economics:'+x for x in economics.get('missing',[])
                if x!='owner_profit_and_fixed_cost_reserve')
 result['not_required_for_relative_comparison']=['unchanged_fixed_costs','generic_profit_target_or_cac_threshold']
 scenario=prospective or {};horizon=scenario.get('horizon') or {}
 start,end=stamp(horizon.get('start')),stamp(horizon.get('end'))
 if not start or not end or not now<=start<end:missing.append('explicit_future_horizon')
 else:result['horizon']=horizon
 terms={}
 for key,meaning in MEANINGS.items():
  term=scenario.get(key)
  if not isinstance(term,dict):missing.append(key);continue
  if (term.get('scope')!=scope or term.get('horizon')!=horizon or term.get('currency')!=BUSINESS.currency
      or term.get('meaning')!=meaning or not term.get('source_ref') or not term.get('assumptions')):
   missing.append(key+':meaning_scope_or_provenance');continue
  try:
   low,high=number(term['lower']),number(term['upper'])
   if low>high or key=='change_cost' and low<0:raise ValueError('Invalid bounds')
   terms[key]=(low,high)
  except (ValueError,KeyError,ArithmeticError):missing.append(key+':invalid_bounds')
 result['missing']=list(dict.fromkeys(missing))
 result['scenario_inputs']=scenario
 if result['missing']:return result
 s,c,k=(terms[name] for name in ('avoidable_spend','foregone_contribution','change_cost'))
 lower=s[0]-c[1]-k[1];upper=s[1]-c[0]-k[0]
 result['benefit_pause_ils']={'lower':str(lower),'upper':str(upper)}
 result['error_cost_bounds_ils']={'false_pause_upper':str(max(Decimal(0),-lower)),
                                'false_continue_upper':str(max(Decimal(0),upper)),
                                'interpretation':'worst-case regret within supplied bounds, not expected loss'}
 if lower>0:
  result.update(status='SCENARIO_PAUSE_DOMINATES',explanation='בגבולות התרחיש בלבד, החיסכון גדול מהתרומה שתאבד ועלות השינוי בכל השילובים.')
 elif upper<0:
  result.update(status='SCENARIO_CONTINUE_DOMINATES',explanation='בגבולות התרחיש בלבד, עצירה מפחיתה את התרומה נטו בכל השילובים.')
 else:
  result.update(status='ABSTAIN',explanation='גבולות התרחיש כוללים תוצאות שבהן עצירה עדיפה ואחרות שבהן אינה עדיפה, או שוויון.')
 result['input_sha256']=hashlib.sha256(json.dumps(scenario,sort_keys=True,allow_nan=False).encode()).hexdigest()
 return result
