"""Fixed-destination read-only connectivity probe. No credential persistence."""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

SHOP='21cc82-3.myshopify.com'
QUERY='query { shop { myshopifyDomain currencyCode } currentAppInstallation { accessScopes { handle } } }'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def connection_status(environ=None, opener=None):
    env=os.environ if environ is None else environ
    result={'checked_at':datetime.now(timezone.utc).isoformat(), 'live_action_allowed':False,
            'credential_storage':'process_environment_only', 'independent_collection':False}
    result['meta']={'state':'requires_configuration' if env.get('META_ACCESS_TOKEN') else 'missing_credential',
                    'verified':False, 'account_id':'393059510164294',
                    'note':'Meta direct provider is not implemented; chat connector is separate'}
    candidates=[env[k] for k in ('SHOPIFY_ADMIN_TOKEN','SHOPIFY_TOKEN') if env.get(k)]
    if not candidates:
        result['shopify']={'state':'missing_credential','verified':False};return result
    # No redirect can forward the token to another host. No arbitrary URL/query accepted.
    open_request=opener or urllib.request.build_opener(NoRedirect()).open
    failures=[]
    for token in dict.fromkeys(candidates):
        request=urllib.request.Request(f'https://{SHOP}/admin/api/2026-10/graphql.json',
            data=json.dumps({'query':QUERY}).encode(),
            headers={'Content-Type':'application/json','X-Shopify-Access-Token':token})
        try:
            with open_request(request,timeout=12) as response:
                raw=response.read(262145)
                if len(raw)>262144:raise ValueError('Response too large')
                data=json.loads(raw)
            shop=(data.get('data') or {}).get('shop') or {}
            if data.get('errors') or shop.get('myshopifyDomain')!=SHOP:
                failures.append('identity_or_permission_not_verified');continue
            scopes=[x['handle'] for x in ((data.get('data') or {}).get('currentAppInstallation') or {}).get('accessScopes',[])]
            result['shopify']={'state':'identity_verified','verified':True,'shop':SHOP,
                               'currency':shop.get('currencyCode'),'reports_scope_present':'read_reports' in scopes,
                               'analytics_verified':False}
            return result
        except urllib.error.HTTPError as error:
            failures.append('credential_rejected' if error.code in (401,403) else 'provider_error')
        except (urllib.error.URLError,TimeoutError,OSError):failures.append('network_error')
        except (ValueError,KeyError,TypeError):failures.append('invalid_response')
    result['shopify']={'state':failures[-1] if failures else 'not_verified','verified':False,'attempts':len(failures)}
    return result
