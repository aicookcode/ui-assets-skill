"""Auditable workflow gates. Evidence is recorded, not proof of message authenticity."""
import hashlib
import json
import re
from copy import deepcopy


def signature(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def confirmation(args, action):
    return validate_confirmation({"text": getattr(args, "user_text", None),
                                 "message_ref": getattr(args, "message_ref", None),
                                 "source": "user_message"}, action)


def validate_confirmation(value, action):
    if (not isinstance(value, dict) or value.get("source") not in
            {"user_message", "interactive_submission"} or
            not isinstance(value.get("text"), str) or not value["text"].strip() or
            not isinstance(value.get("message_ref"), str) or not value["message_ref"].strip()):
        raise ValueError(action + " requires user confirmation evidence: non-empty user text and message reference")
    if len(value["text"]) > 10000 or len(value["message_ref"]) > 1000:
        raise ValueError("confirmation evidence exceeds size limits")
    return {"action": action, "source": value["source"], "text": value["text"].strip(),
            "message_ref": value["message_ref"].strip()}


def scope_binding(p):
    return signature({"project": p["project_id"], "design": p["design_revision"],
                      "sources": p["sources"], "scope": p["scope"]})


def require_scope_confirmation(p):
    record = p.get("scope_confirmation", {})
    if not p.get("scope") or record.get("binding") != scope_binding(p):
        raise ValueError("scope confirmation missing or stale; explicitly confirm the current scope")
    validate_confirmation(record, "scope")


def manifest_binding(p, specs):
    return signature({"scope": scope_binding(p), "revision": p["manifest_revision"], "specs": specs})


def require_manifest_confirmation(p, specs):
    record = p.get("manifest_confirmation", {})
    if record.get("binding") != manifest_binding(p, specs):
        raise ValueError("manifest confirmation missing or stale; confirm the current numbered list")
    validate_confirmation(record, "approve-manifest")


def parse_selection(reply, choices):
    if not isinstance(reply, str) or not reply.strip():
        raise ValueError("numbered selection requires a non-empty reply")
    text = reply.strip()
    if text in {"全部", "全选", "all"}:
        return [c["id"] for c in choices]
    categories = {"只要背景": {"background"}, "只要图标": {"icon"}, "只要插画": {"illustration"},
                  "只要背景和图标": {"background", "icon"}}
    if text in categories:
        ids = [c["id"] for c in choices if c["category"] in categories[text]]
        if not ids:
            raise ValueError("no assets match the selected categories")
        return ids
    tokens = re.split(r"[、,，;；\s]+", text)
    if any(not re.fullmatch(r"[0-9]+", n) for n in tokens):
        raise ValueError("use all or separated selection numbers; put changes in feedback")
    numbers = [int(n) for n in tokens]
    if len(set(numbers)) != len(numbers) or any(n < 1 or n > len(choices) for n in numbers):
        raise ValueError("duplicate or out-of-range selection number")
    return [choices[n-1]["id"] for n in numbers]


REVIEW_CHECKS = ("color", "contour", "details", "proportion", "transparency", "artifacts",
                 "palette", "structure", "new_elements", "text_residue", "seams")


def validate_visual_checks(asset, verdict, note, checks):
    if not isinstance(note, str) or not note.strip():
        raise ValueError("visual review requires a non-empty comparison note")
    required = set(REVIEW_CHECKS)
    if asset.get("composition", {}).get("role") in {"background", "illustration"}:
        required.add("composition")
    if asset.get('composition', {}).get('role') == 'background':
        required.add('module_residue')
    if asset.get("repair_regions"):
        required.add("repair_boundary")
    if asset.get("composition", {}).get("surface"):
        required.update(("container_corners", "edge_padding", "content_position"))
        if asset['composition']['surface'].get('corner_content')=='patterned':
            required.add('corner_texture')
    if asset.get("analysis_findings"):
        required.add("analysis_findings")
    if asset.get("canvas_mapping", {}).get("requires_visual_alignment_review"):
        required.add("canvas_alignment")
    if asset.get('comparison_policy',{}).get('level')=='overall-major-objects':
        required.add('major_objects')
    if asset.get('icon_policy'):
        required.add('icon_fidelity')
    if not isinstance(checks, dict) or not required.issubset(checks):
        raise ValueError("visual review requires checklist entries: " + ", ".join(sorted(required)))
    for key in required:
        entry = checks[key]
        if (not isinstance(entry, dict) or entry.get("verdict") not in {"passed", "issues", "unknown"}
                or not isinstance(entry.get("note"), str) or not entry["note"].strip()):
            raise ValueError("each visual checklist item requires a verdict and comparison note")
    if verdict == "passed" and any(checks[k]["verdict"] != "passed" for k in required):
        raise ValueError("visual pass cannot override checklist issues or unknowns")
    if asset.get('icon_policy'):
        import icon_policy
        icon_policy.review(asset['icon_policy'],checks)
    return {key: deepcopy(checks[key]) for key in sorted(required)}
