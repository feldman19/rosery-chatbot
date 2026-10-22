"""Offline evidence inspection; emits GET descriptors, never sends requests.

Run from any directory: python campaigner/diagnose_asset_groups.py
Official field/type evidence (does not prove placement semantics):
https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/ad.py
https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/adcreativeassetgroupsspec.py
Independent first-hand issue, not a Meta support declaration:
https://github.com/facebook/facebook-python-business-sdk/issues/870
"""
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = "id,status,creative{id},creative_asset_groups_spec"
IDS = {"C11": "120253343694610730", "R01": "120253346008350730",
       "canary": "120253351044960730"}


def inspect_field(ad):
    if "creative_asset_groups_spec" not in ad:
        return {"state": "unknown_missing", "reason": "Field absent in saved response; request coverage unknown."}
    value = ad["creative_asset_groups_spec"]
    if value is None:
        return {"state": "explicit_null", "reason": "Provider representation is null; not proof of absent placement customizations."}
    if not isinstance(value, dict):
        return {"state": "unexpected_type", "type": type(value).__name__}
    groups = value.get("groups")
    return {"state": "present", "keys": sorted(value),
            "group_count": len(groups) if isinstance(groups, list) else None,
            "group_keys": [sorted(g) if isinstance(g, dict) else None for g in groups]
            if isinstance(groups, list) else None,
            "placement_semantics": "unknown; SDK groups is list<Object>, not a placement-mapping contract"}


def load(name):
    return json.loads((ROOT / "outputs" / name).read_text(encoding="utf-8"))


def main():
    start = time.perf_counter()
    saved = load("seven-ads-repair-after-2026-10-19.json")
    canary = load("c11-api-canary-2026-10-18.json")["post_publish_attempt"]
    canary_snapshot = canary["latest_readonly_readback"]
    records = {a["id"]: a for a in saved["ads"]}
    records[IDS["canary"]] = dict(canary_snapshot["ad"], creative=canary_snapshot["creative"])
    result = []
    for label, ad_id in IDS.items():
        ad = records[ad_id]
        creative = ad["creative"]
        spec = creative.get("asset_feed_spec", {})
        result.append({"label": label, "ad_id": ad_id, "creative_id": creative["id"],
                       "status_snapshot": ad["status"], "asset_groups": inspect_field(ad),
                       "known_creative_evidence": {"body_count": len(spec.get("bodies", [])),
                           "optimization_type": spec.get("optimization_type"),
                           "images": spec.get("images", "unknown_not_returned"),
                           "rules": spec.get("asset_customization_rules", "unknown_not_returned")},
                       "future_request_not_executed": {"method": "GET", "relative_path": "v26.0/" + ad_id,
                           "query": {"fields": FIELDS}, "authentication": "Runtime Authorization header only; no token in file/URL"}})
    elapsed = time.perf_counter() - start
    output = {"scope": "offline_saved_evidence_only", "records": result,
              "conclusion": "A GET may reveal an additional representation or hint. Field presence alone does not prove authoring support, mandatory placement mapping, five-copy compatibility or editability; missing/null cannot disprove UI customizations.",
              "timing": {"local_load_decode_compare_seconds": elapsed,
                         "preparation_seconds": None, "provider_execution_seconds": None,
                         "provider_verification_seconds": None, "approval_wait_seconds": None,
                         "note": "No external request executed; missing durations are not zero. Timestamps are not stage durations."},
              "sources": ["https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/ad.py",
                          "https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/adcreativeassetgroupsspec.py",
                          "https://github.com/facebook/facebook-python-business-sdk/issues/870"]}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
