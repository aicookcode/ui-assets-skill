"""Three-round analysis gate, bound to the source, scope and concrete plan."""
from copy import deepcopy
from workflow import signature, scope_binding, validate_confirmation
import icon_policy
import inventory

CHECKS = ("scope", "composition", "method", "format", "source_edges", "mask_plan")
LIMIT = 3


def plan_binding(p, assets):
    specs = []
    for asset in assets:
        item = deepcopy(asset)
        item.pop("selected", None)  # Choosing a subset does not change the analysed plan.
        item.setdefault("transparent", False)
        item.setdefault("trim", False)
        specs.append(item)
    return signature({"scope": scope_binding(p), "plans": specs})


def template(assets):
    result={a["id"]: {"source": a["source"], "reference_box": a["box"],
        "checks": {k: {"verdict": "unknown", "note": "Inspect the exact source crop and proposed plan."}
                   for k in CHECKS}} for a in assets}
    for a in assets:
        if a.get('composition',{}).get('role')=='icon':
            result[a['id']]['icon_policy']={'clarity':'unknown','outline_verifiable':None,
                'details_verifiable':None,'appearance_verifiable':None,'uncertain_features':[],
                'simplifications':[],'approach':None,'reason':'Inspect exact source; prefer SVG. Raster requires an actual failed SVG trial.',
                'svg_trial':None}
    result[inventory.KEY] = inventory.template()
    return result


def check(p, assets, evidence, validate):
    if (not isinstance(assets, list) or not assets or any(not isinstance(a, dict) for a in assets)
            or not isinstance(evidence, dict)):
        raise ValueError("analysis requires a non-empty complete plan and per-asset checks")
    binding = plan_binding(p, assets)
    audit = p.setdefault("analysis_audit", {"scope": scope_binding(p), "rounds": []})
    if audit["scope"] != scope_binding(p) or (audit["rounds"] and
            audit["rounds"][-1]["passed"] and audit["rounds"][-1]["binding"] != binding):
        p.setdefault("analysis_history", []).append(deepcopy(audit))
        audit = {"scope": scope_binding(p), "rounds": []}
        p["analysis_audit"] = audit
    submission = signature({"binding": binding, "evidence": evidence})
    if audit["rounds"] and submission == audit["rounds"][-1]["submission"]:
        if audit["rounds"][-1]["passed"]:
            return audit["rounds"][-1]
        raise ValueError("analysis unchanged: correct the plan or comparison evidence before rechecking")
    if len(audit["rounds"]) >= LIMIT:
        raise ValueError("analysis exhausted three checks; present marked findings and wait for user direction")
    technical, findings = [], []
    try:
        findings.extend(inventory.validate(p, assets, evidence.get(inventory.KEY)))
    except (ValueError, KeyError, TypeError) as exc:
        technical.append({'id': 'batch', 'issue': str(exc)})
    ids = []
    for asset in assets:
        aid = asset.get("id", "<missing>") if isinstance(asset, dict) else "<invalid>"
        ids.append(aid)
        try:
            validate(asset)
        except (ValueError, KeyError, TypeError) as exc:
            technical.append({"id": aid, "issue": str(exc)})
        entry = evidence.get(aid, {})
        if (not isinstance(entry, dict) or entry.get("source") != asset.get("source") or
                entry.get("reference_box") != asset.get("box")):
            technical.append({"id": aid, "issue": "analysis evidence does not identify the exact source crop"})
            continue
        checks = entry.get("checks", {})
        if asset.get('composition',{}).get('role')=='icon':
            try:icon_policy.validate(asset,entry.get('icon_policy'))
            except ValueError as exc:technical.append({'id':aid,'issue':str(exc)})
        for key in CHECKS:
            item = checks.get(key, {}) if isinstance(checks, dict) else {}
            if (not isinstance(item, dict) or item.get("verdict") not in {"passed", "issues", "unknown"}
                    or not isinstance(item.get("note"), str) or not item["note"].strip()):
                technical.append({"id": aid, "issue": "missing analysis check: " + key})
            elif item["verdict"] != "passed":
                findings.append({"id": aid, "check": key, **item})
    if len(set(ids)) != len(ids):
        technical.append({"id": "batch", "issue": "duplicate asset ID"})
    result = {"round": len(audit["rounds"])+1, "limit": LIMIT, "binding": binding,
              "submission": submission, "passed": not technical and not findings,
              "technical_errors": technical, "findings": findings, "evidence": deepcopy(evidence),
              "marked": bool(technical or findings), "can_export": False}
    result["items"] = [{"id": a.get("id"), "name": a.get("name"), "source": a.get("source"),
                        "box": a.get("box"), "format": a.get("format"), "method": a.get("method"),
                        "marked": any(f["id"] in {a.get("id"), "batch"} for f in technical + findings)}
                       for a in assets]
    audit["rounds"].append(result)
    audit.pop("allowance", None)
    p["phase"] = ("ASSET_ANALYSIS" if result["passed"] else
                  "ANALYSIS_REVIEW" if result["round"] == LIMIT else "ANALYSIS_FIX_REQUIRED")
    return result


def allow(p, evidence):
    evidence = validate_confirmation(evidence, "allow-analysis")
    audit = p.get("analysis_audit", {})
    rounds = audit.get("rounds", [])
    if audit.get("scope") != scope_binding(p) or len(rounds) != LIMIT or rounds[-1]["passed"]:
        raise ValueError("allow-analysis requires the current exhausted three-round analysis")
    if rounds[-1]["technical_errors"]:
        raise ValueError("invalid coordinates, masks or missing evidence must be corrected; only unresolved visual findings can be allowed")
    audit["allowance"] = {"binding": rounds[-1]["binding"], "confirmation": evidence}
    p["phase"] = "ASSET_ANALYSIS"


def require(p, assets):
    audit = p.get("analysis_audit", {})
    rounds = audit.get("rounds", [])
    binding = plan_binding(p, assets)
    if not rounds or audit.get("scope") != scope_binding(p) or rounds[-1]["binding"] != binding:
        raise ValueError("analysis-check missing or stale for the current source/scope/plan")
    # Recheck current policy and retained evidence; old passed audits are not exemptions.
    inventory_findings = inventory.validate(p, assets, rounds[-1]['evidence'].get(inventory.KEY))
    if rounds[-1]['passed'] and inventory_findings:
        raise ValueError('source inventory second scan is unresolved; reanalyse')
    for asset in assets:
        if asset.get('selected', True) and asset.get('composition', {}).get('role') == 'icon':
            icon_policy.validate(asset, rounds[-1]['evidence'].get(asset['id'], {}).get('icon_policy'))
    if rounds[-1]["passed"]:
        return
    allowance = audit.get("allowance", {})
    if (len(rounds) == LIMIT and not rounds[-1]["technical_errors"] and
            allowance.get("binding") == binding):
        validate_confirmation(allowance.get("confirmation"), "allow-analysis")
        return
    raise ValueError("analysis has unresolved findings; correct and recheck, or obtain user permission after round three")
