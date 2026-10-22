"""Fixed-exposure Poisson intervals: conditional diagnostics, not Meta validity proof.

Equal-tail inversion of the Poisson CDF (Garwood interval). Requires integer counts,
fixed planned observation and independent events at a constant rate. Real Meta
attribution/adaptive delivery can violate these assumptions. No sequential coverage.
"""
from math import exp, log, lgamma, isfinite


def _cdf(k, mean):
    if mean == 0:return 1.0
    logs=[i*log(mean)-lgamma(i+1)-mean for i in range(k+1)]
    peak=max(logs)
    return min(1.0,exp(peak)*sum(exp(x-peak) for x in logs))


def _inverse_cdf_mean(k, target):
    low,high=0.0,max(1.0,float(k+1))
    while _cdf(k,high)>target:high*=2
    for _ in range(80):
        mid=(low+high)/2
        if _cdf(k,mid)>target:low=mid
        else:high=mid
    return (low+high)/2


def poisson_rate_interval(count, exposure, alpha):
    if type(count) is not int or not 0<=count<=10000:raise ValueError('Exact integer count in supported range required')
    if type(exposure) not in (int,float) or not isfinite(exposure) or exposure<=0:raise ValueError('Positive finite fixed exposure required')
    if type(alpha) not in (int,float) or not isfinite(alpha) or not 0<alpha<1:raise ValueError('Explicit error probability required')
    lo=0.0 if count==0 else _inverse_cdf_mean(count-1,1-alpha/2)
    hi=_inverse_cdf_mean(count,alpha/2)
    return {'rate_lower':lo/exposure,'rate_upper':hi/exposure,'alpha':alpha,
            'cpa_lower':exposure/hi,'cpa_upper':exposure/lo if lo else None,
            'cpa_upper_unbounded':count==0,'model':'fixed_exposure_poisson_equal_tail',
            'conditional_on_model_assumptions':True,'causal_claim_allowed':False,
            'adaptive_allocation_validated':False,'selection_and_multiplicity_validated':False,
            'sequential_monitoring_validated':False}


def one_event_sensitivity(spend, purchases):
    """Deterministic sensitivity, not a CI; valid for displaying recorded outcomes."""
    if (type(spend) not in (int,float) or not isfinite(spend) or spend<0
        or type(purchases) not in (int,float) or not isfinite(purchases) or purchases<0):return None
    return {'reported_cpa':spend/purchases if purchases else None,
            'cpa_if_one_more_reported':spend/(purchases+1),
            'cpa_if_one_less_reported':spend/(purchases-1) if purchases>1 else None,
            'scope':'arithmetic_sensitivity_not_statistical_interval_or_forecast'}
