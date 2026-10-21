"""Portable UI asset project CLI. All coordinates are EXIF-normalized pixel coordinates."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from uuid import uuid4
from zipfile import ZipFile, ZIP_DEFLATED

from PIL import ImageDraw

from imaging import load, box_valid, transform, qa, nine_slice, contact_sheet, svg_check, preserve_reference, repair_mask
from providers import request_for, api_generate
from composition import validate_composition, validate_blending
from review import render_review, validate_submission, review_token
from quality_flow import release_problems, repair_findings, phase_for
from fidelity import validate_regions, measure_regions
from surface import validate_surface, measure_surface, complete_corners, corner_mask, measure_background_ui
import source_extent
import analysis_loop
import failed_candidates
import review_summary
import export_reviewed
import svg_validation
import canvas
from workflow import (confirmation, validate_confirmation, scope_binding, require_scope_confirmation,
                      manifest_binding, require_manifest_confirmation, parse_selection, validate_visual_checks, signature)


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", value):
        raise ValueError("IDs, pages and categories require 1..64 ASCII letters/digits/_/-")
    return value


def inside(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("project path escapes project directory")
    return path


@contextmanager
def locked(root):
    """A project has one writer. The lock survives hard interruption for explicit recovery."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".studio.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError("project is locked; if its writer has stopped, remove .studio.lock and resume") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)


def event(p, action, **details):
    p["events"].append({"time": now(), "action": action, **details})


def save(root, p):
    p["updated_at"] = now()
    write(root / "project.json", p)


def source(p, sid):
    try:
        return next(s for s in p["sources"] if s["id"] == sid)
    except StopIteration:
        raise ValueError("unknown source: " + sid) from None


def asset_by_id(p, aid):
    try:
        return next(a for a in p["assets"] if a["id"] == aid)
    except StopIteration:
        raise ValueError("unknown asset: " + aid) from None


def current(asset):
    return next((v for v in asset["versions"] if v["number"] == asset["current_version"]), None)


def record_source(root, path, sid):
    im = load(path)
    target = root / "sources" / (sid + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    original = target.with_name(sid + "-original" + Path(path).suffix.lower())
    shutil.copyfile(path, original)
    im.save(target)
    return {"id": sid, "file": str(target.relative_to(root)), "original": str(original.relative_to(root)),
            "size": list(im.size), "sha256": digest(target)}


def ensure_baseline(root, p):
    if not p["design_confirmed"]:
        raise ValueError("design has not been confirmed by the user")
    for s in p["sources"]:
        if digest(inside(root, s["file"])) != s["sha256"]:
            raise ValueError("design baseline was modified: " + s["id"])


def init(root, args):
    if (root / "project.json").exists():
        raise ValueError("project already exists")
    if args.mode == "B" and not args.sources:
        raise ValueError("mode B requires at least one design image")
    sources = [record_source(root, path, f"source-{i+1}") for i, path in enumerate(args.sources)]
    p = {"schema_version": 3, "project_id": uuid4().hex, "mode": args.mode,
         "phase": "DESIGN_PENDING" if args.mode == "A" else "SCOPE_RESOLUTION",
         "design_confirmed": args.mode == "B", "design_revision": 1, "sources": sources,
         "scope": None, "manifest_revision": 0, "approved_revision": None,
         "assets": [], "archived_assets": {}, "events": [], "created_at": now(),
         "provider": {"backend": "native", "max_attempts": 2}}
    event(p, "init", mode=args.mode)
    save(root, p)
    return p


def set_design(root, p, args):
    if p["mode"] != "A" or p["design_confirmed"]:
        raise ValueError("design replacement is only allowed before mode A design approval; use a new project for a new baseline")
    p["design_revision"] += 1
    p["sources"] = [record_source(root, path, f"design-{p['design_revision']}-{i+1}")
                    for i, path in enumerate(args.sources)]
    event(p, "design-update", revision=p["design_revision"])
    save(root, p)


def set_scope(root, p, data, evidence=None):
    ensure_baseline(root, p)
    evidence = validate_confirmation(evidence, "scope")
    regions = data.get("regions")
    if not isinstance(regions, list) or not regions:
        raise ValueError("scope requires non-empty regions (use --all for all images)")
    for region in regions:
        s = source(p, region["source"])
        if not box_valid(region["box"], s["size"]):
            raise ValueError("scope box outside source")
        if "page" in region:
            safe_id(region["page"])
        if "categories" in region:
            for category in region["categories"]:
                safe_id(category)
    p["scope"] = data
    p["scope_confirmation"] = evidence | {"binding": scope_binding(p), "time": now()}
    p.pop("manifest_confirmation", None)
    p["approved_revision"] = None
    p["phase"] = "ASSET_ANALYSIS"
    event(p, "scope", scope=data)
    save(root, p)


def allowed(p, a):
    for r in p["scope"]["regions"]:
        b, rb = a["box"], r["box"]
        if (a["source"] == r["source"] and rb[0] <= b[0] < b[2] <= rb[2]
                and rb[1] <= b[1] < b[3] <= rb[3]
                and (not r.get("page") or a["page"] == r["page"])
                and (not r.get("categories") or a["category"] in r["categories"])):
            return True
    return False


FIELDS = {"id", "name", "page", "category", "source", "box", "selected", "method", "format", "size",
          "transparent", "trim", "remove_background", "input_file", "occluded", "prompt", "nine_slice",
          "relations", "adaptation", "difficulty", "repair_regions", "matte_background",
          "composition", "edge_blend", "background_join", "alpha_policy", "fidelity_regions",'matte_preserve_regions'}


def validate_asset(p, a, check_scope=True):
    if set(a) - FIELDS:
        raise ValueError("unknown manifest fields: " + str(sorted(set(a) - FIELDS)))
    for key in ("id", "page", "category"):
        safe_id(a[key])
    if not isinstance(a.get("name"), str) or not a["name"].strip():
        raise ValueError("asset requires a name")
    s = source(p, a["source"])
    if not box_valid(a["box"], s["size"]) or (check_scope and not allowed(p, a)):
        raise ValueError("asset outside selected scope: " + a["id"])
    if a["method"] not in {"reuse", "crop", "vector", "repair", "generate"}:
        raise ValueError("unsupported processing method")
    if a["format"] not in {"png", "webp", "svg"}:
        raise ValueError("supported formats: png, webp, svg")
    if a["format"] == "svg" and a["method"] not in {"reuse", "vector"}:
        raise ValueError("raster assets cannot be converted to SVG by renaming")
    if a.get("size") is not None and (not isinstance(a["size"], list) or len(a["size"]) != 2
            or not all(type(v) is int and 0 < v <= 16384 for v in a["size"])):
        raise ValueError("size requires two positive integers <=16384")
    for key in ("selected", "transparent", "trim", "occluded"):
        if key in a and type(a[key]) is not bool:
            raise ValueError(key + " must be boolean")
    if a.get("nine_slice"):
        margins = a["nine_slice"]
        if a["format"] == "svg" or not isinstance(margins, list) or len(margins) != 4 or not all(type(v) is int and v > 0 for v in margins):
            raise ValueError("nine_slice requires four positive pixel margins on a raster asset")
    if a.get("occluded") and a["method"] == "crop":
        raise ValueError("occluded asset requires repair/generate or a clean original resource")
    if a.get("repair_regions"):
        if a["method"] != "repair" or a["format"] == "svg":
            raise ValueError("repair_regions requires raster repair method")
        repair_mask((a["box"][2]-a["box"][0], a["box"][3]-a["box"][1]), a["repair_regions"])
        if a.get("size") or a.get("trim") or a.get("remove_background") or a.get("matte_background"):
            raise ValueError("masked repair must preserve reference canvas; transform in a separate asset")
    if a.get("matte_background") and not re.fullmatch(r"#[0-9a-fA-F]{6}", a["matte_background"]):
        raise ValueError("matte_background requires #RRGGBB")
    if 'matte_preserve_regions' in a:
        size=(a['box'][2]-a['box'][0],a['box'][3]-a['box'][1])
        if (not a.get('matte_background') or not isinstance(a['matte_preserve_regions'],list)
            or any(not box_valid(b,size) for b in a['matte_preserve_regions'])):
            raise ValueError('matte_preserve_regions requires valid foreground boxes and matte_background')
    validate_composition(a, required=p.get("schema_version", 1) >= 2)
    if a.get("alpha_policy", "inspect") not in {"inspect", "solid"}:
        raise ValueError("alpha_policy must be inspect or solid")
    c = a.get("composition", {})
    if p.get("schema_version", 1) >= 2 and c.get("role") == "background":
        canvas = c.get("background", {}).get("canvas_box")
        if not box_valid(canvas, s["size"]):
            raise ValueError("background requires a valid page canvas_box")
        regions = [r for r in p.get("scope", {}).get("regions", [])
                   if r.get("source") == a["source"] and
                   (not r.get("page") or r["page"] == a["page"]) and
                   (not r.get("categories") or a["category"] in r["categories"])]
        page_regions = [r for r in regions if r.get("page") == a["page"]]
        if not any(r["box"] == canvas for r in (page_regions or regions)):
            raise ValueError("background canvas_box must match an approved page scope; register the full page, not only the top artwork")
        if c["background"]["strategy"] == "full-base" and a["box"] != canvas:
            raise ValueError("full-base must cover the complete page canvas_box, not a preview fragment")
    if p.get("schema_version", 1) >= 2 and a["method"] == "repair" and not a.get("repair_regions"):
        raise ValueError("repair requires explicit repair_regions to preserve visible pixels")
    if (a.get("background_join") or a.get("edge_blend")) and not a.get("repair_regions"):
        raise ValueError("background blending requires explicit repair_regions to protect the artwork")
    validate_blending(a, (a["box"][2]-a["box"][0],a["box"][3]-a["box"][1]))
    validate_regions(a)
    if a.get("selected", True):
        validate_surface(a)


def spec(a):
    return {key: a[key] for key in FIELDS if key in a}


def png_fallback(root, p, previous, proposed):
    """One source-raster stage after two reviewed SVG candidates, never a retry reset."""
    if not (previous and previous.get('format') == 'svg' and proposed.get('format') == 'png'
            and previous.get('attempts') == 2):
        return None
    if previous.get('png_fallback') or previous['category'] != 'icon':
        raise ValueError('PNG fallback is available only once per SVG icon round')
    allowed_changes = {'format', 'method', 'input_file', 'transparent', 'trim',
                       'remove_background', 'matte_background', 'matte_preserve_regions', 'composition'}
    before = {k:v for k,v in spec(previous).items() if k not in allowed_changes}
    after = {k:v for k,v in proposed.items() if k not in allowed_changes}
    if (before != after or proposed.get('method') != 'crop'
            or not proposed.get('transparent') or not proposed.get('trim')
            or proposed.get('input_file')):
        raise ValueError('PNG fallback must extract the same source icon with transparent tight bounds')
    records = [c for c in previous.get('candidate_history', [])
               if c['cycle'] == previous.get('generation_cycle') and c.get('version')]
    reviewed = []
    for attempt in (1, 2):
        candidates = [c for c in records if c['attempt'] == attempt]
        if not candidates:
            raise ValueError('PNG fallback requires two actual SVG candidates and source comparisons')
        v = next(v for v in previous['versions'] if v['number'] == candidates[-1]['version'])
        review = v['quality'].get('visual_review', {})
        checks = review.get('checks', {})
        if (review.get('verdict') not in {'issues', 'unknown'}
                or checks.get('icon_fidelity', {}).get('verdict') not in {'issues', 'unknown'}):
            raise ValueError('PNG fallback requires two unresolved SVG source comparisons')
        verify_version(root, previous, v)
        validate_visual_checks(previous, review['verdict'], review.get('note'), checks)
        svg_validation.validate(root, previous, v, source(p, previous['source']), checks)
        reviewed.append(v['number'])
    return {'svg_attempts': 2, 'svg_versions': reviewed, 'svg_cycle': previous['generation_cycle'],
            'png_limit': 2, 'time': now(), 'reason': 'two unresolved SVG source comparisons'}


def manifest(root, p, data):
    ensure_baseline(root, p)
    if not p["scope"]:
        raise ValueError("resolve scope before asset analysis")
    require_scope_confirmation(p)
    if not isinstance(data, list):
        raise ValueError("manifest must be an array")
    old = {**p.get("archived_assets", {}), **{a["id"]: a for a in p["assets"]}}
    updated, seen = [], set()
    original_confirmation = deepcopy(p.get('manifest_confirmation'))
    original_ids = {a['id'] for a in p['assets']}
    original_approved = (p.get('approved_revision') == p['manifest_revision'] and original_confirmation
                         and original_confirmation.get('binding') == manifest_binding(p, [spec(a) for a in p['assets']]))
    fallback_ids, other_changes = [], False
    # Validate everything before any project state changes.
    for entry in data:
        a = deepcopy(entry)
        previous = old.get(a.get("id"))
        a.setdefault("selected", previous["selected"] if previous else True)
        a.setdefault("transparent", False)
        a.setdefault("trim", False)
        preserved_outside_scope = previous and not a["selected"] and (
            {k: v for k, v in spec(previous).items() if k != "selected"}
            == {k: v for k, v in a.items() if k != "selected"})
        validate_asset(p, a, check_scope=not preserved_outside_scope)
        if a["selected"]:
            s = source(p, a["source"])
            original=load(inside(root, s['file']))
            source_extent.require_complete(source_extent.inspect(original,a))
            validate_surface(a, original.crop(a["box"]))
        if a["id"] in seen:
            raise ValueError("duplicate asset ID")
        seen.add(a["id"])
        if previous and previous["acceptance"] in {"accepted", "accepted_difference"} and (
                {k: v for k, v in spec(previous).items() if k != "selected"}
                != {k: v for k, v in a.items() if k != "selected"}):
            raise ValueError("locked asset must be explicitly revised: " + a["id"])
        state = {"status": "pending", "acceptance": "pending", "versions": [], "current_version": None,
                 "attempts": 0, "reason": "initial extraction", "last_error": None, "acceptance_evidence": None,
                 "candidate_history": [], "generation_cycle": None, "png_fallback": None}
        if previous:
            state = {k: deepcopy(previous.get(k, value)) for k, value in state.items()}
            # Selection alone must never invalidate a completed task.
            before, after = spec(previous), deepcopy(a)
            before.pop("selected", None)
            after.pop("selected", None)
            if before != after:
                fallback = png_fallback(root, p, previous, a)
                if previous["attempts"] >= 2 and not fallback:
                    raise ValueError("exhausted generation requires explicit user revise/feedback before changing the specification")
                # Internal plan corrections are still the same bounded repair
                # round. Only explicit user revise/feedback starts a new budget.
                state.update(status="pending", acceptance="pending", current_version=None,
                             reason="manifest changed")
                if fallback:
                    state.update(attempts=0, png_fallback=fallback, generation_cycle=uuid4().hex,
                                 last_error=None, acceptance_evidence=None)
                    fallback_ids.append(a['id'])
                else:
                    other_changes = True
            if spec(previous) != a and a['id'] not in fallback_ids:
                other_changes = True
        else:
            other_changes = True
        updated.append({**a, **state})
    analysis_loop.require(p, [spec(a) for a in updated])
    if p.get("review_feedback"):
        before = [{k: v for k, v in spec(a).items() if k != "selected"} for a in p["assets"]]
        after = [{k: v for k, v in spec(a).items() if k != "selected"} for a in updated]
        if before == after:
            raise ValueError("review feedback requires a changed manifest before reapproval")
    p["archived_assets"] = {aid: a for aid, a in old.items() if aid not in seen}
    p["assets"] = updated
    p.pop("review_feedback", None)
    p["manifest_revision"] += 1
    p.pop("manifest_confirmation", None)
    p["approved_revision"] = None
    p["phase"] = "MANIFEST_REVIEW"
    # Only this narrow, analyzed fallback inherits the existing selection approval.
    # Changes to scope, membership, geometry or unrelated assets still need approval.
    if fallback_ids and not other_changes and original_ids == seen and original_approved:
        p['manifest_confirmation'] = original_confirmation | {
            'binding': manifest_binding(p, [spec(a) for a in updated]),
            'derived_policy': 'svg-two-then-png-two', 'fallback_ids': fallback_ids, 'time': now()}
        p['approved_revision'] = p['manifest_revision']
        p['phase'] = 'ASSET_PROCESSING'
        event(p, 'png-fallback', assets=fallback_ids, png_limit=2)
    # Snapshot preserves deleted/merged asset histories and original manifest specs.
    write(root / "history" / f"manifest-{p['manifest_revision']:04d}.json", p)
    event(p, "manifest", revision=p["manifest_revision"])
    save(root, p)
    previews(root, p, analysis=True)


def require_review_ready(root, p, assets=None):
    approved(root, p)
    for a in assets if assets is not None else [a for a in p["assets"] if a["selected"]]:
        v = current(a)
        problems = release_problems(a, v)
        if v:
            verify_version(root, a, v)
            q = v["quality"]
            review = q.get("visual_review")
            if review:
                try:
                    validate_visual_checks(a, review.get("verdict"), review.get("note"), review.get("checks"))
                except ValueError:
                    problems.append("visual checklist is missing or obsolete")
            if review and (review.get("output_sha256") != digest(inside(root, v["file"])) or
                           review.get("reference", {}).get("sha256") != source(p, a["source"])["sha256"]):
                problems.append("visual evidence is stale")
            if a['format']=='svg' and review:
                try:
                    svg_validation.validate(root,a,v,source(p,a['source']),review.get('checks',{}))
                except ValueError as exc:
                    problems.append(str(exc))
            preview = q.get("render_preview")
            if preview:
                if (preview.get("vector_sha256") != digest(inside(root, v["file"])) or
                        digest(inside(root, preview["file"])) != preview.get("sha256")):
                    problems.append("SVG render evidence is stale")
        if problems:
            raise ValueError("quality gate blocks user review: " + a["id"] + ": " + "; ".join(problems))


def previews(root, p, analysis=False, internal=False):
    if not analysis and not internal:
        require_review_ready(root, p)
    dest = root / ("inspections" if internal else "previews") / (f"manifest-{p['manifest_revision']:04d}" if analysis else "results")
    dest.mkdir(parents=True, exist_ok=True)
    items = []
    annotated = {}
    for a in p["assets"]:
        if analysis:
            s = source(p, a["source"])
            base = load(inside(root, s["file"]))
            crop = dest / (a["id"] + ".png")
            base.crop(a["box"]).save(crop)
            items.append((a["id"], crop))
            if a["source"] not in annotated:
                annotated[a["source"]] = base.copy()
            draw = ImageDraw.Draw(annotated[a["source"]])
            l, t, r, b = a["box"]
            draw.rectangle((l, t, r-1, b-1), outline="#ff0055", width=2)
            draw.text((l+2, t+2), a["id"], fill="#ff0055", stroke_width=1, stroke_fill="white")
        else:
            v = current(a)
            if a["selected"] and v:
                preview = v["quality"].get("render_preview")
                path = inside(root, preview["file"] if preview else v["file"])
                label = (a["id"] + " auto:" + v["quality"]["status"] + " visual:" +
                         v["quality"].get("reviewed_status", "pending")) if internal else a["id"] + " passed"
                items.append((label, path))
    for sid, image in annotated.items():
        image.save(dest / (sid + "-annotated.png"))
    contact_sheet(items, dest / "overview.png")
    if analysis:
        write(dest / "choices.json", numbered_choices(p))
    write(dest / "manifest.json", [spec(a) if analysis else public_asset(a) for a in p["assets"]])
    return dest


def prepare_repair(root, p, a):
    """Create one actionable host task per failed version, without user reapproval."""
    v = current(a)
    findings = repair_findings(v) if v else [a.get("last_error") or "processing failed"]
    if a.get("last_error") and a["last_error"] not in findings:
        findings.append(a["last_error"])
    limit = 2
    a["acceptance"] = "pending"
    a["acceptance_evidence"] = None
    if a["attempts"] >= limit:
        a["status"] = "repair_blocked"
        event(p, "repair-blocked", asset=a["id"], findings=findings, attempts=a["attempts"])
        return
    failure_binding = signature({"spec": spec(a), "version": a["current_version"], "findings": findings})
    task_path = root / "requests" / (a["id"] + ".json")
    if task_path.exists():
        previous = read(task_path)
        if previous.get("failure_binding") == failure_binding:
            a["status"] = "awaiting_repair"
            return
    s = source(p, a["source"])
    crop = root / "requests" / "references" / (a["id"] + ".png")
    crop.parent.mkdir(parents=True, exist_ok=True)
    load(inside(root, s["file"])).crop(a["box"]).save(crop)
    task = request_for(p, a, inside(root, s["file"]))
    task.update(request_id=uuid4().hex, kind="vector_revision" if a["format"] == "svg" else "asset_repair",
                reference_crop=str(crop.resolve()), project_id=p["project_id"], asset_id=a["id"],
                reference_crop_sha256=digest(crop), reference_policy="exact_crop",
                manifest_revision=p["manifest_revision"], spec_sha256=signature(spec(a)),
                reference_sha256=s["sha256"], failed_version=a["current_version"],
                failure_binding=failure_binding, findings=findings,
                remaining_attempts=limit-a["attempts"],
                invariants="Keep overall composition, palette, major objects, scale and placement. "
                           "Do not add/remove obvious subjects such as a bouquet; ignore isolated petal/texture variation. "
                           "Declared corner completion is outside content identity QA but must have clean edges. "
                           "Remove only approved UI/text. "
                           "Never relax the gate, change format, or expand repair regions to hide a mismatch.")
    task["prompt"] += "\nCorrect the following failed checks: " + "; ".join(findings)
    if a.get("repair_regions"):
        canvas.prepare(task,crop)
    if v:
        task["previous_output"] = str(inside(root, v["file"]))
    write(task_path, task)
    a["status"] = "awaiting_repair"
    event(p, "auto-repair-queued", asset=a["id"], version=a["current_version"], findings=findings)


def update_quality_state(root, p, a):
    v = current(a)
    if v and repair_findings(v):
        prepare_repair(root, p, a)
    elif release_problems(a, v):
        a["status"] = "awaiting_visual_review"
    else:
        a["status"] = "ready"
    p["phase"] = phase_for(p["assets"])


def numbered_choices(p):
    return {"project_id": p["project_id"], "manifest_revision": p["manifest_revision"],
            "analysis_findings": (p.get("analysis_audit", {}).get("rounds", [{}])[-1].get("findings", [])
                                  if p.get("analysis_audit", {}).get("allowance") else []),
            "review_token": review_token(p), "interaction": "numbered",
            "feedback": p.get("review_feedback"),
            "choices": [{"number": i, "id": a["id"], "name": a["name"], "page": a["page"],
                         "category": a["category"], "method": a["method"], "format": a["format"],
                         "selected": a["selected"], "box": a["box"],
                         "size": a.get("size") or [a["box"][2]-a["box"][0], a["box"][3]-a["box"][1]],
                         "decision": a.get("composition", {}).get("decision"),
                         "occluded": a.get("occluded", False)}
                        for i, a in enumerate(p["assets"], 1)]}


def check_expressed_selection(p, evidence):
    text = evidence["text"]
    is_choice = (text in {"全部", "全选", "all", "只要背景", "只要图标", "只要插画", "只要背景和图标"}
                 or re.fullmatch(r"[0-9、,，;；\s]+", text))
    if is_choice:
        expressed = set(parse_selection(text, p["assets"]))
        selected = {a["id"] for a in p["assets"] if a["selected"]}
        if expressed != selected:
            raise ValueError("confirmation names different assets; apply the numbered selection first")


def approve_manifest(root, p, evidence):
    ensure_baseline(root, p)
    evidence = validate_confirmation(evidence, "approve-manifest")
    validate_execution(p)
    if p["phase"] != "MANIFEST_REVIEW" or not any(a["selected"] for a in p["assets"]):
        raise ValueError("no pending manifest with selected assets")
    check_expressed_selection(p, evidence)
    selected = [a["id"] for a in p["assets"] if a["selected"]]
    p["manifest_confirmation"] = evidence | {
        "binding": manifest_binding(p, [spec(a) for a in p["assets"]]),
        "selected_ids": selected, "time": now()}
    p.update(approved_revision=p["manifest_revision"], phase="ASSET_PROCESSING")
    event(p, "approve-manifest", revision=p["manifest_revision"], evidence=p["manifest_confirmation"])
    save(root, p)


def record_feedback(root, p, text, evidence, ids=None):
    ensure_baseline(root, p)
    require_scope_confirmation(p)
    evidence = validate_confirmation(evidence, "manifest-feedback")
    if not p.get("assets") or not isinstance(text, str) or not text.strip():
        raise ValueError("manifest feedback requires an existing list and non-empty text")
    targets = [asset_by_id(p, aid) for aid in ids] if ids else []
    if not ids and any(a["acceptance"] in {"accepted", "accepted_difference"} for a in p["assets"]):
        raise ValueError("specify affected assets when giving feedback on accepted outputs")
    for a in targets:
        a.update(status="pending", acceptance="needs_changes", current_version=None,
                 attempts=0, reason=text.strip(), last_error=None, acceptance_evidence=None, generation_cycle=uuid4().hex)
    p["review_feedback"] = {"text": text.strip(), "revision": p["manifest_revision"], "evidence": evidence}
    p["approved_revision"] = None
    p.pop("manifest_confirmation", None)
    p["phase"] = "MANIFEST_REVIEW"
    event(p, "manifest-feedback", evidence=evidence, text=text.strip())
    save(root, p)
    previews(root, p, analysis=True)


def validate_execution(p):
    require_scope_confirmation(p)
    if p.get("review_feedback"):
        raise ValueError("resolve review feedback and reimport the manifest before approval")
    for a in p["assets"]:
        if a["selected"]:
            validate_composition(a, required=True)
            validate_asset(p, spec(a))
    analysis_loop.require(p, [spec(a) for a in p["assets"]])


def import_selection(root, p, data):
    ensure_baseline(root, p)
    if p["phase"] != "MANIFEST_REVIEW":
        raise ValueError("selection requires a pending manifest")
    selected, feedback = validate_submission(p, data)
    evidence = validate_confirmation(data.get("confirmation"), "approve-manifest")
    specs = [spec(a) | {"selected": a["id"] in selected} for a in p["assets"]]
    # Validate the whole submission before changing selections or revisions.
    candidate = deepcopy(p)
    candidate["assets"] = specs
    candidate.pop("review_feedback", None)
    if not feedback:
        validate_execution(candidate)
        check_expressed_selection(candidate, evidence)
    manifest(root, p, specs)
    event(p, "import-selection", assets=sorted(selected), feedback=feedback)
    if feedback:
        p["review_feedback"] = {"text": feedback, "revision": p["manifest_revision"], "evidence": evidence}
        save(root, p)
        previews(root, p, analysis=True)
        return
    approve_manifest(root, p, evidence)


def public_asset(a):
    v = current(a)
    return {k: deepcopy(a[k]) for k in ("id", "name", "page", "category", "box", "selected", "format", "status", "acceptance")} | {
        "current_version": a["current_version"], "quality": deepcopy(v["quality"]) if v else None,
        "adaptation": a.get("adaptation"), "last_error": a["last_error"]}


def approved(root, p):
    ensure_baseline(root, p)
    if p["approved_revision"] != p["manifest_revision"] or not p["manifest_revision"]:
        raise ValueError("current manifest has not been explicitly approved")
    validate_execution(p)
    require_manifest_confirmation(p, [spec(a) for a in p["assets"]])



def finish(root, p, a, input_path=None, retained_candidate=None):
    s = source(p, a["source"])
    base = load(inside(root, s["file"]))
    extent=source_extent.inspect(base,a,root/'analysis/source-contexts')
    source_extent.require_complete(extent)
    ref = base.crop(a["box"])
    candidate = retained_candidate or failed_candidates.archive(root, a, input_path, ref, spec(a))
    versions_root = root / "versions" / a["id"]
    versions_root.mkdir(parents=True, exist_ok=True)
    # Include orphan directories left by hard interruptions; never overwrite history.
    existing = [int(d.name[1:]) for d in versions_root.glob("v*") if d.is_dir() and d.name[1:].isdigit()]
    number = max([v["number"] for v in a["versions"]] + existing + [0]) + 1
    directory = versions_root / f"v{number:04d}"
    directory.mkdir()
    target = directory / (a["id"] + "." + a["format"])
    warnings = []
    offset, slices = [0, 0], None
    if a["format"] == "svg":
        if not input_path:
            raise ValueError("SVG requires a real reconstructed vector file")
        svg_check(input_path)
        shutil.copyfile(input_path, target)
        proof_path=Path(input_path).with_suffix('.bounds.json')
        if proof_path.is_file():
            proof=read(proof_path)
            if proof.get('output_sha256')!=digest(target):raise ValueError('tight SVG bounds proof is stale')
            write(directory/'content-bounds.json',proof)
            old=proof['original_viewbox'];new=proof['content_viewbox']
            offset=[(new[0]-old[0])/old[2]*(a['box'][2]-a['box'][0]),
                    (new[1]-old[1])/old[3]*(a['box'][3]-a['box'][1])]
        quality = qa(target, a)
        if a.get("size"):
            quality["notes"].append("SVG viewBox is scalable; target_size is recommended display size.")
    else:
        im = load(input_path) if input_path else ref.copy()
        protected = None
        if a.get("repair_regions"):
            task_path = root / "requests" / (a["id"] + ".json")
            mapping = (retained_candidate.get('canvas_mapping') if retained_candidate and 'canvas_mapping' in retained_candidate
                       else read(task_path).get("canvas_mapping") if task_path.exists() else None)
            candidate['canvas_mapping']=deepcopy(mapping)
            im, alignment = canvas.normalize(im,ref,mapping)
            generated_corners=im.copy()
            write(directory / "canvas-mapping.json",alignment)
            blend_evidence=[]
            execution_regions = deepcopy(a['repair_regions'])
            if a.get('composition', {}).get('role') == 'background':
                # Match color only in fully bounded UI windows. Full-width bands
                # and corner wedges have no surrounding paper on all four sides.
                ui_boxes = [r['box'] for r in a['composition']['surface']['source_ui']['regions']]
                execution_regions = [
                    {'box':r,'blend':'boundary'} if isinstance(r,list) and r in ui_boxes
                    and r[0]>1 and r[1]>1 and r[2]<ref.width-1 and r[3]<ref.height-1 else r
                    for r in execution_regions]
            im, editable = preserve_reference(im, ref, execution_regions,blend_evidence)
            write(directory/'repair-blend.json',blend_evidence)
            im, completed = complete_corners(im,ref,a,generated_corners)
            completed.save(directory / 'corner-completion-mask.png')
            editable.save(directory / "repair-mask.png")
            protected = editable.point(lambda value: 255-value)
            warnings.append("Only explicit repair regions were edited; original pixels outside are protected.")
        im, offset, transform_warnings = transform(im, a)
        warnings.extend(transform_warnings)
        im.save(target, format="PNG" if a["format"] == "png" else "WEBP", lossless=True)
        quality = qa(target, a, ref, warnings)
        if a.get("repair_regions"):
            quality["canvas_mapping"] = alignment
            quality['repair_blending']=blend_evidence
            quality['comparison_policy']={'level':'overall-major-objects',
                'excluded_corner_mask':str((directory/'corner-completion-mask.png').relative_to(root)),
                'ignore':'Individual petals, tiny speckles and small texture differences',
                'reject':'Added/missing major objects or obvious changes in composition, subject scale or position'}
            quality['corner_completion']={'method':('generated-pattern-harmonic-blend' if a.get('composition',{}).get('surface',{}).get('corner_content')=='patterned' else 'smooth-boundary-color-interpolation'),
                'pixels':sum(completed.histogram()[1:]),'within_approved_mask':True}
        fidelity_issues, measurements = measure_regions(load(target), ref, a.get("fidelity_regions", []))
        surface_issues, surface_measurements = measure_surface(load(target), ref, a)
        fidelity_issues.extend(surface_issues)
        quality["surface_edge_anchors"] = surface_measurements
        module_issues, module_measurements = measure_background_ui(load(target), ref, a)
        fidelity_issues.extend(module_issues)
        if a.get('composition', {}).get('role') == 'background':
            quality['background_ui_regions'] = module_measurements
        quality["fidelity_regions"] = measurements
        if fidelity_issues:
            quality["issues"].extend(fidelity_issues)
            quality["status"] = "issues"
        if protected is not None:
            from PIL import ImageChops
            diff = ImageChops.difference(load(target), ref)
            channels = diff.split()
            delta = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), ImageChops.lighter(channels[2], channels[3]))
            unchanged = ImageChops.multiply(delta, protected).getbbox() is None
            quality["protected_pixels_equal"] = unchanged
            quality["repair_regions"] = a["repair_regions"]
            if not unchanged:
                quality["issues"].append("protected pixels changed")
                quality["status"] = "issues"
            if ref.getchannel("A").getextrema()[0] == 255 and not a.get("transparent") and load(target).getchannel("A").getextrema()[0] != 255:
                quality["issues"].append("masked repair unexpectedly removed opacity")
                quality["status"] = "issues"
        # Review evidence: reference/result on both light and dark backdrops.
        from PIL import Image
        comparison = Image.new("RGB", (640, 480), "white")
        for index, (view, color) in enumerate(((ref, "#faf7ef"), (im, "#faf7ef"),
                                                (ref, "#263630"), (im, "#263630"))):
            tile = Image.new("RGBA", (320, 240), color)
            view = view.copy()
            view.thumbnail((300, 220))
            tile.alpha_composite(view, ((320-view.width)//2, (240-view.height)//2))
            comparison.paste(tile.convert("RGB"), ((index % 2)*320, (index // 2)*240))
        comparison.save(directory / "comparison.png")
        # Content QA excludes automatic corner completion; full comparison remains
        # available for seam and edge quality inspection.
        excluded=corner_mask(a,ref.size)
        if excluded.getbbox():
            from PIL import Image
            panel=Image.new('RGBA',(ref.width*2,ref.height),'#faf7ef')
            for i,view in enumerate((ref,im)):
                marked=view.copy();marked.paste((160,180,195,255),(0,0),excluded)
                panel.paste(marked,(i*ref.width,0))
            panel.save(directory/'content-comparison.png')
            quality['content_comparison']=str((directory/'content-comparison.png').relative_to(root))
        quality["comparison"] = str((directory / "comparison.png").relative_to(root))
        if a.get('composition', {}).get('role') == 'background':
            # Full-size evidence exposes internal module traces without new cards
            # covering them; the generic thumbnail sheet is not sufficient.
            from PIL import ImageDraw
            audit_view = Image.new('RGB', (ref.width*2, ref.height), '#faf7ef')
            marked = ref.convert('RGB').copy()
            draw = ImageDraw.Draw(marked)
            for region in a['composition']['surface']['source_ui']['regions']:
                x0,y0,x1,y1 = region['box']
                draw.rectangle((x0,y0,x1-1,y1-1), outline='#df3b32', width=1)
            audit_view.paste(marked, (0,0))
            audit_view.paste(im.convert('RGB'), (ref.width,0))
            audit_view.save(directory/'background-module-audit.png')
            quality['background_module_audit'] = str((directory/'background-module-audit.png').relative_to(root))
        if a.get("nine_slice"):
            slices = nine_slice(im, a["nine_slice"], directory / "slices")
            write(directory / "nine-slice.json", {"margins_ltrb": a["nine_slice"], "size": list(im.size),
                                                   "slices": slices, "page_responsive_tested": False})
    if a.get("occluded"):
        quality["notes"].append("Occluded background reconstructed; hidden original pixels are unknown.")
        if quality["status"] == "passed":
            quality["status"] = "unknown"
    if extent is not None:
        quality['source_extent']=extent
    audit = p.get("analysis_audit", {})
    findings = ([f for f in audit["rounds"][-1]["findings"] if f["id"] in {a["id"], "batch"}]
                if audit.get("allowance") else [])
    if findings:
        quality["analysis_findings"] = findings
        if quality["status"] == "passed":
            quality["status"] = "unknown"
    icon_evidence=p.get('analysis_audit',{}).get('rounds',[{}])[-1].get('evidence',{}).get(a['id'],{}).get('icon_policy')
    if icon_evidence and (a['format']=='svg' or icon_evidence.get('clarity')!='clear'):
        quality['icon_policy']=deepcopy(icon_evidence)
        quality['status']='unknown' if quality['status']=='passed' else quality['status']
    related = [f for f in directory.rglob("*") if f.is_file()]
    version = {"number": number, "created_at": now(), "reason": a["reason"], "method": a["method"],
               "file": str(target.relative_to(root)), "quality": quality, "trim_offset": offset,
               "files": [{"path": str(f.relative_to(root)), "sha256": digest(f)} for f in related],
               "spec": spec(a)}
    a["versions"].append(version)
    if candidate.get('version') is not None:
        candidate.setdefault('version_history',[]).append(candidate['version'])
    candidate["version"] = number
    candidate['processor_sha256']=processor_signature()
    a.update(current_version=number, status="quality_failed" if quality["status"] == "issues" else "ready",
             acceptance="pending", last_error=None)
    event(p, "version", asset=a["id"], number=number)
    update_quality_state(root, p, a)


def processor_signature():
    files=('studio.py','canvas.py','surface.py','fidelity.py','imaging.py','repair_blend.py')
    return signature({name:digest(Path(__file__).with_name(name)) for name in files})


def reprocess_candidates(root,p,ids,evidence):
    """Re-run changed deterministic rules on retained bytes; never call a provider."""
    approved(root,p)
    records=[]
    for aid in ids:
        a=asset_by_id(p,aid)
        deterministic_crop=a.get('method')=='crop' and a.get('matte_background')
        if not a['selected'] or a.get('format')=='svg' or not (a.get('repair_regions') or deterministic_crop):
            raise ValueError('reprocess-candidates requires selected masked repairs or source matte crops')
        candidates=[c for c in a.get('candidate_history',[]) if c['cycle']==a.get('generation_cycle')
                    and c['spec_sha256']==signature(spec(a))]
        for c in candidates:
            if digest(root/c['file'])!=c['sha256']:raise ValueError('retained candidate hash changed')
            if c.get('processor_sha256')==processor_signature():continue
            if not c['readable']:continue
            finish(root,p,a,root/c['file'],retained_candidate=c)
            records.append({'id':aid,'attempt':c['attempt'],'version':c['version']})
    event(p,'reprocess-candidates',evidence=evidence,records=records,generation_calls=0)
    p['phase']=phase_for(p['assets']);save(root,p)
    return {'reprocessed':records,'generation_calls':0,'requires_new_visual_review':True}


def input_problem(asset, path=None, required=False):
    """Missing or unreadable inputs are prerequisites, not generation attempts."""
    if path is None:
        path=asset.get('input_file')
    if not path:
        return 'input_file is required for reuse/vector' if required or asset['method'] in {'reuse','vector'} else None
    try:
        if not Path(path).is_file():
            return 'input file is not ready: '+str(path)
        svg_check(path) if asset['format']=='svg' else load(path)
    except Exception as exc:
        return 'input file is unreadable or invalid: '+str(exc)
    return None


def await_input(root,p,asset,reason):
    changed=asset['status']!='awaiting_input' or asset.get('last_error')!=reason
    asset.update(status='awaiting_input',last_error=reason)
    if changed:event(p,'awaiting-input',asset=asset['id'],reason=reason,attempts=asset['attempts'])
    save(root,p)


def process(root, p, provider=None, evidence=None):
    approved(root, p)
    config = read(provider) if provider else {"backend": "native", "max_attempts": 2}
    if provider:
        evidence = validate_confirmation(evidence, "api-execution")
        event(p, "provider-authorization", backend=config.get("backend"), evidence=evidence)
    if config.get("backend") not in {"native", "api"}:
        raise ValueError("backend must be native or api")
    # Config is kept outside the project; only safe non-secret fields are persisted.
    if int(config.get("max_attempts", 2)) != 2:
        raise ValueError("generation limit is fixed at two attempts, including the initial candidate")
    p["provider"] = {"backend": config["backend"], "max_attempts": 2}
    p["phase"] = "ASSET_PROCESSING"
    save(root, p)
    for a in p["assets"]:
        if a["selected"] and current(a) and a["status"] in {"ready", "quality_failed", "awaiting_visual_review"}:
            update_quality_state(root, p, a)
        if not a["selected"] or a["status"] in {"ready", "awaiting_native", "awaiting_repair",
                                                "awaiting_visual_review", "repair_blocked", "accepted"}:
            continue
        if a["status"] == "quality_failed":
            prepare_repair(root, p, a)
            continue
        if a["attempts"] >= p["provider"]["max_attempts"]:
            continue
        # Recheck awaiting_input on every invocation; files appearing at the
        # approved path resume without revise or budget reset.
        if a['method'] not in {'generate','repair'}:
            problem=input_problem(a)
            if problem:
                await_input(root,p,a,problem)
                continue
        while a["attempts"] < p["provider"]["max_attempts"]:
            a.update(status='processing',last_error=None)
            attempt_started=False
            save(root, p)
            try:
                if a["method"] in {"generate", "repair"}:
                    s = source(p, a["source"])
                    task = request_for(p, a, inside(root, s["file"]))
                    crop_path = root / "requests" / "references" / (a["id"] + ".png")
                    crop_path.parent.mkdir(parents=True, exist_ok=True)
                    load(inside(root, s["file"])).crop(a["box"]).save(crop_path)
                    task["reference_crop"] = str(crop_path.resolve())
                    task["reference_crop_sha256"] = digest(crop_path)
                    task["reference_policy"] = "exact_crop"
                    task["manifest_revision"] = p["manifest_revision"]
                    task["project_id"] = p["project_id"]
                    task["spec_sha256"] = signature(spec(a))
                    task["reference_sha256"] = s["sha256"]
                    if a.get("repair_regions"):
                        canvas.prepare(task,crop_path)
                    write(root / "requests" / (a["id"] + ".json"), task)
                    if config["backend"] == "native":
                        a["status"] = "awaiting_native"
                        break
                    a['attempts']+=1
                    attempt_started=True
                    save(root,p)
                    data = api_generate(config, task)
                    incoming = root / "incoming" / (a["id"] + "-" + uuid4().hex + ".png")
                    incoming.parent.mkdir(exist_ok=True)
                    incoming.write_bytes(data)
                    finish(root, p, a, incoming)
                else:
                    input_path = a.get("input_file")
                    a['attempts']+=1
                    attempt_started=True
                    save(root,p)
                    finish(root, p, a, input_path)
                break
            except Exception as exc:
                if not attempt_started:
                    await_input(root,p,a,'input preparation failed: '+str(exc))
                    break
                a["status"] = "failed"
                a["last_error"] = "provider failed" if config["backend"] == "api" and a["method"] in {"generate", "repair"} else str(exc)
                history = a.get("candidate_history", [])
                if history and history[-1]["attempt"] == a["attempts"]:
                    history[-1]["error"] = a["last_error"]
            finally:
                save(root, p)
    p["phase"] = phase_for(p["assets"])
    save(root, p)
    previews(root, p, internal=True)


def fulfill(root, p, args):
    approved(root, p)
    a = asset_by_id(p, args.asset)
    if a["status"] not in {"awaiting_native", "awaiting_repair"} or not a["selected"]:
        raise ValueError("asset is not waiting for a native result")
    if a["attempts"] >= p["provider"]["max_attempts"]:
        raise ValueError("attempt limit reached; use revise for a new user-authorized attempt")
    task = read(root / "requests" / (a["id"] + ".json"))
    if a["status"] == "awaiting_repair" and getattr(args, "request_id", None) != task.get("request_id"):
        raise ValueError("repair result requires the current request_id")
    crop = root / "requests" / "references" / (a["id"] + ".png")
    if (not crop.is_file() or task.get("reference_crop_sha256") != digest(crop) or
            getattr(args, "reference_sha256", None) != task.get("reference_crop_sha256")):
        raise ValueError("result requires the current exact crop reference_sha256; whole-page reference is not sufficient")
    if (task.get("project_id") != p["project_id"] or task.get("asset_id") != a["id"] or
            task.get("manifest_revision") != p["manifest_revision"] or
            task.get("spec_sha256") != signature(spec(a)) or
            task.get("reference_sha256") != source(p, a["source"])["sha256"]):
        raise ValueError("native request is stale; revise the asset and regenerate its request")
    problem=input_problem(a,args.file,required=True)
    if problem:
        a['last_error']=problem
        event(p,'result-input-not-ready',asset=a['id'],reason=problem,attempts=a['attempts'])
        save(root,p)
        raise ValueError(problem)
    a["attempts"] += 1
    try:
        finish(root, p, a, args.file)
        current(a)["quality"]["input_reference_sha256"] = args.reference_sha256
    except Exception as exc:
        a["last_error"] = str(exc)
        history = a.get("candidate_history", [])
        if history and history[-1]["attempt"] == a["attempts"]:
            history[-1]["error"] = str(exc)
        prepare_repair(root, p, a)
        p["phase"] = phase_for(p["assets"])
        raise
    finally:
        save(root, p)
    previews(root, p, internal=True)


def revise(root, p, ids, reason, evidence=None):
    # An older output may violate a newly strengthened contract. Let the human
    # request its revision; processing still requires fresh, valid analysis.
    ensure_baseline(root,p)
    require_scope_confirmation(p)
    if p['approved_revision']!=p['manifest_revision']:
        raise ValueError('current manifest has not been explicitly approved')
    require_manifest_confirmation(p,[spec(a) for a in p['assets']])
    evidence = validate_confirmation(evidence, "revise")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("revision requires non-empty feedback")
    assets = [asset_by_id(p, aid) for aid in ids]
    for a in assets:
        a.update(status="pending", acceptance="needs_changes", attempts=0, reason=reason,
                 last_error=None, current_version=None, generation_cycle=uuid4().hex)
    p["phase"] = "ASSET_PROCESSING"
    event(p, "revise", assets=ids, reason=reason, evidence=evidence)
    save(root, p)


def verify_version(root, a, v):
    for entry in v["files"]:
        path = inside(root, entry["path"])
        if not path.is_file() or digest(path) != entry["sha256"]:
            raise ValueError("version file missing/modified: " + entry["path"])
        if path.suffix in {".png", ".webp"}:
            load(path)
        elif path.suffix == ".svg":
            svg_check(path)
    target = inside(root, v["file"])
    if target.suffix == ".svg":
        svg_check(target)
    else:
        load(target)


def accept(root, p, ids, difference=False, evidence=None):
    approved(root, p)
    evidence = validate_confirmation(evidence, "accept-differences" if difference else "accept")
    assets = [asset_by_id(p, aid) for aid in ids]
    for a in assets:
        v = current(a)
        if not a["selected"] or not v:
            raise ValueError("asset has no selected current output: " + a["id"])
        verify_version(root, a, v)
        if v["quality"]["issues"]:
            raise ValueError("technical file errors must be resolved before acceptance")
        if v["quality"].get("reviewed_status") == "issues" and not difference:
            raise ValueError("visual review found differences; revise or explicitly accept differences")
        if v["quality"]["status"] != "passed" and v["quality"].get("reviewed_status") != "passed" and not difference:
            raise ValueError("quality is not passed; record visual-review or explicit acceptance of differences")
    require_review_ready(root, p)
    for a in assets:
        a.update(acceptance="accepted_difference" if difference else "accepted", status="accepted")
    for a in assets:
        a["acceptance_evidence"] = evidence | {"version": a["current_version"], "time": now()}
    event(p, "accept", assets=ids, difference=difference, evidence=evidence)
    save(root, p)


def visual_review(root, p, args):
    approved(root, p)
    a = asset_by_id(p, args.asset)
    if a["acceptance"] in {"accepted", "accepted_difference"}:
        raise ValueError("locked asset; use revise before further changes")
    v = current(a)
    if not v:
        raise ValueError("asset has no output")
    verify_version(root, a, v)
    if v["quality"]["issues"] and args.verdict == "passed":
        raise ValueError("visual review cannot clear technical errors")
    checks = validate_visual_checks({**a, "analysis_findings": v["quality"].get("analysis_findings"),
                                    "canvas_mapping":v["quality"].get("canvas_mapping",{}),
                                    'comparison_policy':v['quality'].get('comparison_policy',{}),
                                    'icon_policy':v['quality'].get('icon_policy')}, args.verdict, args.note,
                                    read(args.checks_file) if args.checks_file else None)
    if a["format"] == "svg" and not v["quality"].get("render_preview"):
        raise ValueError("SVG visual review requires a rendered preview of the current vector")
    render = v["quality"].get("render_preview")
    if render and (render["vector_sha256"] != digest(inside(root, v["file"])) or
                   render["sha256"] != digest(inside(root, render["file"]))):
        raise ValueError("SVG render evidence is stale")
    s = source(p, a["source"])
    if a['format']=='svg':
        svg_validation.validate(root,a,v,s,checks)
    v["quality"]["visual_review"] = {"verdict": args.verdict, "note": args.note.strip(),
        "checks": checks, "time": now(), "reference": {"source": s["id"], "sha256": s["sha256"],
        "box": a["box"]}, "output_sha256": digest(inside(root, v["file"])),
        "comparison": v["quality"].get("comparison")}
    # Human/model visual review remains separate from objective automatic status.
    v["quality"]["reviewed_status"] = args.verdict
    event(p, "visual-review", asset=a["id"], verdict=args.verdict, note=args.note)
    update_quality_state(root, p, a)
    save(root, p)


def register_render(root, p, args):
    approved(root, p)
    a = asset_by_id(p, args.asset)
    v = current(a)
    if a["format"] != "svg" or not v or v["number"] != args.version:
        raise ValueError("render preview must target the current SVG version")
    if a["acceptance"] in {"accepted", "accepted_difference"}:
        raise ValueError("locked asset; use revise before replacing render evidence")
    verify_version(root, a, v)
    vector_sha = digest(inside(root, v["file"]))
    if args.vector_sha256 != vector_sha:
        raise ValueError("render preview does not match the current SVG hash")
    size = svg_check(inside(root, v["file"]))
    image = load(args.file)
    if not image.getchannel("A").getbbox():
        raise ValueError("render preview is empty")
    if abs(image.width * size[1] - image.height * size[0]) > max(size):
        raise ValueError("render preview aspect ratio differs from SVG viewBox")
    target = root / "inspections" / "svg" / a["id"] / (uuid4().hex + ".png")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    v["quality"]["render_preview"] = {"file": str(target.relative_to(root)), "sha256": digest(target),
        "vector_sha256": vector_sha, "version": v["number"], "size": list(image.size)}
    v['quality']['source_comparison']=svg_validation.build(root,a,v,source(p,a['source']))
    render_quality = qa(target, {**a, "size": None, "trim": False})
    if a.get('composition',{}).get('role')=='icon':
        bounds=image.getchannel('A').getbbox()
        margins=[bounds[0],bounds[1],image.width-bounds[2],image.height-bounds[3]]
        render_quality['content_bounds']=list(bounds)
        render_quality['outer_margins']=margins
        v['quality']['icon_bounds']={'render_size':list(image.size),'content_bounds':list(bounds),'outer_margins':margins}
        if max(margins)>1:
            render_quality['issues'].append('SVG icon has exterior blank space; tighten its viewBox using a real render')
            render_quality['status']='issues'
    v["quality"]["render_issues"] = render_quality["issues"]
    v["quality"]["issues"] = list(render_quality["issues"])
    v["quality"].pop("visual_review", None)
    v["quality"].pop("reviewed_status", None)
    update_quality_state(root, p, a)
    event(p, "render-preview", asset=a["id"], version=v["number"], vector_sha256=vector_sha)
    save(root, p)


def restore(root, p, args):
    approved(root, p)
    evidence = confirmation(args, "restore")
    a = asset_by_id(p, args.asset)
    v = next((v for v in a["versions"] if v["number"] == args.version), None)
    if not v:
        raise ValueError("unknown version")
    verify_version(root, a, v)
    restored_spec = deepcopy(v["spec"])
    validate_asset(p, restored_spec)
    if {k: v for k, v in spec(a).items() if k != "selected"} != {k: v for k, v in restored_spec.items() if k != "selected"}:
        raise ValueError("version uses a different manifest spec; reimport that spec and reapprove before restoring")
    a.update(current_version=args.version, acceptance="pending", status="ready", acceptance_evidence=None)
    update_quality_state(root, p, a)
    event(p, "restore", asset=a["id"], version=args.version, evidence=evidence)
    save(root, p)
    previews(root, p, internal=True)


def deliver(root, p):
    approved(root, p)
    require_review_ready(root, p)
    accepted = [a for a in p["assets"] if a["selected"] and a["acceptance"] in {"accepted", "accepted_difference"}]
    if not accepted:
        raise ValueError("no user-accepted assets to deliver")
    require_review_ready(root, p, accepted)
    for a in accepted:
        v = current(a)
        record = a.get("acceptance_evidence", {})
        validate_confirmation(record, "acceptance")
        if record.get("version") != a["current_version"]:
            raise ValueError("acceptance evidence does not match the current version")
        if not v or v["quality"]["issues"]:
            raise ValueError("invalid accepted asset")
        verify_version(root, a, v)
    p["phase"] = "DELIVERY"
    save(root, p)
    delivery = root / "deliveries" / ("delivery-" + uuid4().hex[:12])
    delivery.mkdir(parents=True)
    exported, preview_items = [], []
    for a in accepted:
        v = current(a)
        directory = delivery / a["page"] / a["category"] / a["id"]
        directory.mkdir(parents=True)
        version_dir = inside(root, v["file"]).parent
        for f in v["files"]:
            src = inside(root, f["path"])
            relative = src.relative_to(version_dir)
            dest = directory / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
        target = directory / Path(v["file"]).name
        item = public_asset(a)
        preview = v["quality"].get("render_preview")
        if preview:
            rendered = directory / "render-preview.png"
            shutil.copyfile(inside(root, preview["file"]), rendered)
            item["quality"]["render_preview"]["file"] = rendered.relative_to(delivery).as_posix()
        item.update(file=target.relative_to(delivery).as_posix(), version=v["number"],
                    size=v["quality"]["size"], trim_offset=v["trim_offset"],
                    source=a["source"], relations=a.get("relations", []))
        exported.append(item)
        preview_items.append((a["id"], inside(root, preview["file"]) if preview else target))
    contact_sheet(preview_items, delivery / "overview.png")
    refs = delivery / "references"
    refs.mkdir()
    for s in p["sources"]:
        shutil.copyfile(inside(root, s["file"]), refs / (s["id"] + ".png"))
    excluded = [public_asset(a) for a in p["assets"] if a["selected"] and a not in accepted]
    write(delivery / "manifest.json", {"project_id": p["project_id"], "manifest_revision": p["manifest_revision"], "assets": exported})
    write(delivery / "quality-report.json", {"assets": exported, "excluded": excluded,
          "limitations": ["Standalone asset processing does not test page layout; consult any separately supplied downstream render evidence.",
                          "Automatic checks and user acceptance are separate.",
                          "Hidden original pixels cannot be proven identical after reconstruction."]})
    report = ["# Quality report", "", f"Delivered: {len(exported)}; excluded: {len(excluded)}.", "",
              "Standalone asset processing does not test page layout; consult any separately supplied downstream render evidence. Resampling does not recover original detail.", ""]
    for item in exported + excluded:
        report.append(f"- {item['id']}: {item['status']}; user: {item['acceptance']}; quality: {item['quality']}")
    (delivery / "quality-report.md").write_text("\n".join(report), encoding="utf-8")
    files = [f for f in delivery.rglob("*") if f.is_file()]
    checksums = {str(f.relative_to(delivery)).replace("\\", "/"): digest(f) for f in files}
    write(delivery / "checksums.json", checksums)
    archive = delivery.with_suffix(".zip")
    with ZipFile(archive, "w", ZIP_DEFLATED) as z:
        for path in delivery.rglob("*"):
            if path.is_file():
                z.write(path, str(path.relative_to(delivery)).replace("\\", "/"))
    with ZipFile(archive) as z:
        if z.testzip() is not None:
            raise ValueError("ZIP integrity check failed")
        for filename, checksum in checksums.items():
            if hashlib.sha256(z.read(filename)).hexdigest() != checksum:
                raise ValueError("ZIP content hash mismatch")
    p["phase"] = "COMPLETED"
    p["last_delivery"] = {"directory": str(delivery.relative_to(root)), "zip": str(archive.relative_to(root))}
    event(p, "deliver", **p["last_delivery"])
    save(root, p)
    return {"directory": str(delivery), "zip": str(archive), "assets": len(exported), "excluded": len(excluded)}


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", required=True, type=Path)
    sub = ap.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("init")
    cmd.add_argument("--mode", choices=["A", "B"], required=True)
    cmd.add_argument("--sources", nargs="*", default=[])
    cmd = sub.add_parser("design")
    cmd.add_argument("--sources", nargs="+", required=True)
    sub.add_parser("approve-design")
    cmd = sub.add_parser("scope")
    group = cmd.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--file", type=Path)
    cmd = sub.add_parser("manifest")
    cmd.add_argument("--file", type=Path, required=True)
    cmd = sub.add_parser("analysis-template")
    cmd.add_argument("--file", type=Path, required=True)
    cmd = sub.add_parser("analysis-check")
    cmd.add_argument("--file", type=Path, required=True)
    cmd.add_argument("--checks-file", type=Path, required=True)
    sub.add_parser("allow-analysis")
    sub.add_parser("revise-analysis")
    sub.add_parser("approve-manifest")
    sub.add_parser("review-ui")
    sub.add_parser("choices")
    cmd = sub.add_parser("select")
    cmd.add_argument("--reply", default="")
    cmd.add_argument("--feedback", default="")
    cmd.add_argument("--manifest-revision", type=int, required=True)
    cmd.add_argument("--review-token", required=True)
    cmd = sub.add_parser("feedback")
    cmd.add_argument("--text", required=True)
    cmd.add_argument("--assets", nargs="+")
    cmd = sub.add_parser("import-selection")
    cmd.add_argument("--file", type=Path, required=True)
    cmd = sub.add_parser("process")
    cmd.add_argument("--provider", type=Path)
    cmd = sub.add_parser("fulfill")
    cmd.add_argument("--asset", required=True)
    cmd.add_argument("--file", type=Path, required=True)
    cmd.add_argument("--request-id")
    cmd.add_argument("--reference-sha256")
    cmd = sub.add_parser("render-preview")
    cmd.add_argument("--asset", required=True)
    cmd.add_argument("--file", type=Path, required=True)
    cmd.add_argument("--version", type=int, required=True)
    cmd.add_argument("--vector-sha256", required=True)
    cmd = sub.add_parser("revise")
    cmd.add_argument("--assets", nargs="+", required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("accept")
    cmd.add_argument("--assets", nargs="+", required=True)
    cmd.add_argument("--accept-differences", action="store_true")
    cmd = sub.add_parser("visual-review")
    cmd.add_argument("--asset", required=True)
    cmd.add_argument("--verdict", choices=["passed", "issues", "unknown"], required=True)
    cmd.add_argument("--note", required=True)
    cmd.add_argument("--checks-file", type=Path)
    cmd = sub.add_parser("reject")
    cmd.add_argument("--assets", nargs="+", required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("restore")
    cmd.add_argument("--asset", required=True)
    cmd.add_argument("--version", type=int, required=True)
    sub.add_parser("deliver")
    cmd=sub.add_parser('export-reviewed')
    cmd.add_argument('--summary',type=Path,required=True)
    cmd.add_argument('--user-text',required=True)
    cmd.add_argument('--message-ref',required=True)
    sub.add_parser("status")
    sub.add_parser("preview")
    cmd = sub.add_parser("review-summary")
    cmd.add_argument("--file",help="One best-candidate selection and comparison reason per failed asset")
    cmd = sub.add_parser('reprocess-candidates')
    cmd.add_argument('--assets',nargs='+',required=True)
    sub.add_parser("inspect")
    sub.add_parser("repair-queue")
    cmd = sub.add_parser("failed-preview")
    cmd.add_argument("--file", type=Path, required=True)
    for name in ("scope", "approve-design", "approve-manifest", "select", "feedback",
                 "accept", "revise", "restore", "reject", "review-ui", "process", "allow-analysis", "revise-analysis",'reprocess-candidates'):
        sub.choices[name].add_argument("--user-text")
        sub.choices[name].add_argument("--message-ref")
    return ap


def run(args):
    root = args.project.resolve()
    if args.command == "status":
        p = read(root / "project.json")
        return {"phase": p["phase"], "manifest_revision": p["manifest_revision"],
                "approved_revision": p["approved_revision"], "sources": p["sources"],
                "assets": [public_asset(a) for a in p["assets"]],
                "analysis": p.get("analysis_audit", {}).get("rounds", [None])[-1],
                "notices": generation_notices(p)}
    with locked(root):
        if args.command == "init":
            return init(root, args)
        p = read(root / "project.json")
        cmd = args.command
        if cmd == "design":
            set_design(root, p, args)
        elif cmd == "approve-design":
            if p["mode"] != "A" or not p["sources"] or p["phase"] != "DESIGN_PENDING":
                raise ValueError("no pending generated design")
            evidence = confirmation(args, "approve-design")
            p["design_confirmation"] = evidence | {"design_revision": p["design_revision"], "time": now()}
            p.update(design_confirmed=True, phase="SCOPE_RESOLUTION")
            event(p, "approve-design", revision=p["design_revision"], evidence=evidence)
            save(root, p)
        elif cmd == "scope":
            data = {"text": "full image", "regions": [{"source": s["id"], "box": [0, 0, *s["size"]]} for s in p["sources"]]} if args.all else read(args.file)
            set_scope(root, p, data, confirmation(args, "scope"))
        elif cmd == "manifest":
            manifest(root, p, read(args.file))
        elif cmd == "analysis-template":
            return analysis_loop.template(read(args.file))
        elif cmd == "analysis-check":
            ensure_baseline(root, p)
            require_scope_confirmation(p)
            data = read(args.file)
            def validate_plan(a):
                validate_asset(p, a, check_scope=a.get("selected", True))
                if a.get("selected", True):
                    s = source(p, a["source"])
                    original=load(inside(root, s['file']))
                    source_extent.require_complete(source_extent.inspect(original,a,root/'analysis/source-contexts'))
                    validate_surface(a, original.crop(a["box"]))
            report = analysis_loop.check(p, data, read(args.checks_file), validate_plan)
            folder = root / "analysis" / ("cycle-" + signature(p["analysis_audit"])[0:12])
            write(folder / "plan.json", data)
            write(folder / "report.json", report)
            event(p, "analysis-check", round=report["round"], passed=report["passed"], report=str(folder.relative_to(root)))
            save(root, p)
            return {"phase": p["phase"], "report": str(folder / "report.json"), **report}
        elif cmd == "allow-analysis":
            ensure_baseline(root, p)
            require_scope_confirmation(p)
            analysis_loop.allow(p, confirmation(args, cmd))
            event(p, cmd, binding=p["analysis_audit"]["allowance"]["binding"])
            save(root, p)
        elif cmd == "revise-analysis":
            evidence = confirmation(args, cmd)
            if p.get("analysis_audit"):
                p.setdefault("analysis_history", []).append(p.pop("analysis_audit"))
            p["approved_revision"] = None
            p.pop("manifest_confirmation", None)
            p["phase"] = "ASSET_ANALYSIS"
            event(p, cmd, evidence=evidence)
            save(root, p)
        elif cmd == "choices":
            ensure_baseline(root, p)
            require_scope_confirmation(p)
            if p["phase"] != "MANIFEST_REVIEW":
                raise ValueError("numbered choices require a pending manifest")
            return numbered_choices(p)
        elif cmd == "select":
            evidence = confirmation(args, "select")
            choices = numbered_choices(p)
            ids = parse_selection(args.reply, choices["choices"]) if args.reply.strip() else (
                [a["id"] for a in p["assets"] if a["selected"]] if args.feedback.strip() else [])
            import_selection(root, p, {"action": "confirm-selection", "project_id": p["project_id"],
                "manifest_revision": args.manifest_revision, "review_token": args.review_token,
                "selected_ids": ids, "feedback": args.feedback, "confirmation": evidence})
        elif cmd == "feedback":
            record_feedback(root, p, args.text, confirmation(args, "manifest-feedback"), args.assets)
        elif cmd == "review-ui":
            evidence = confirmation(args, "interactive-review")
            ensure_baseline(root, p)
            if p["phase"] != "MANIFEST_REVIEW":
                raise ValueError("checkbox review requires a pending manifest")
            require_scope_confirmation(p)
            dest = previews(root, p, analysis=True)
            render_review(root, p, dest / "review.html")
            event(p, "interactive-review", evidence=evidence)
            save(root, p)
            return {"file": str(dest / "review.html"), "inline_file": str(dest / "review-inline.html")}
        elif cmd == "import-selection":
            import_selection(root, p, read(args.file))
        elif cmd == "approve-manifest":
            approve_manifest(root, p, confirmation(args, "approve-manifest"))
        elif cmd == "process":
            process(root, p, args.provider, confirmation(args, "api-execution") if args.provider else None)
        elif cmd == "fulfill":
            fulfill(root, p, args)
        elif cmd == "revise":
            revise(root, p, args.assets, args.reason, confirmation(args, "revise"))
        elif cmd == "accept":
            accept(root, p, args.assets, args.accept_differences, confirmation(args, "accept-differences" if args.accept_differences else "accept"))
        elif cmd == "visual-review":
            visual_review(root, p, args)
        elif cmd == "render-preview":
            register_render(root, p, args)
        elif cmd == "repair-queue":
            approved(root, p)
            tasks = []
            for a in p["assets"]:
                if a["selected"] and a["status"] == "awaiting_repair":
                    tasks.append(read(root / "requests" / (a["id"] + ".json")))
            return {"phase": p["phase"], "tasks": tasks,
                    "blocked": [a["id"] for a in p["assets"] if a["selected"] and a["status"] == "repair_blocked"]}
        elif cmd == "reject":
            approved(root, p)
            evidence = confirmation(args, "reject")
            assets = [asset_by_id(p, aid) for aid in args.assets]
            for a in assets:
                a["acceptance"] = "rejected"
            event(p, "reject", assets=args.assets, reason=args.reason, evidence=evidence)
            save(root, p)
        elif cmd == "restore":
            restore(root, p, args)
        elif cmd == 'export-reviewed':
            evidence=confirmation(args,'export-reviewed')
            result=export_reviewed.export(root,p,args.summary,evidence,current,lambda a,v:verify_version(root,a,v))
            p['last_delivery']=result;p['phase']='COMPLETED'
            event(p,'export-reviewed',evidence=evidence,**result);save(root,p)
            return result
        elif cmd == "deliver":
            return deliver(root, p)
        elif cmd == "preview":
            return {"directory": str(previews(root, p))}
        elif cmd == "review-summary":
            approved(root, p)
            folder, report = review_summary.build(root, p["assets"], spec, current, release_problems,
                                                  lambda a,v:verify_version(root,a,v),read(args.file) if args.file else None)
            write(folder/"report.json",report)
            event(p,cmd,report=str((folder/"report.json").relative_to(root)))
            save(root,p)
            return {"directory":str(folder),**report}
        elif cmd == 'reprocess-candidates':
            return reprocess_candidates(root,p,args.assets,confirmation(args,'reprocess-candidates'))
        elif cmd == "failed-preview":
            approved(root, p)
            folder, report = failed_candidates.diagnostic(root, p["assets"], read(args.file), spec,
                release_problems, lambda a, v: verify_version(root, a, v))
            write(folder / "report.json", report)
            event(p, cmd, report=str((folder / "report.json").relative_to(root)))
            save(root, p)
            return {"directory":str(folder), **report}
        elif cmd == "inspect":
            approved(root, p)
            return {"internal_only": True, "directory": str(previews(root, p, internal=True))}
        return {"phase": p["phase"], "assets": [public_asset(a) for a in p["assets"]],
                "notices": generation_notices(p)}


def generation_notices(p):
    notices = []
    for a in p["assets"]:
        if a['selected'] and a['status']=='awaiting_input':
            notices.append({'asset_id':a['id'],'code':'input_not_ready',
                'message':'输入尚未准备好；不消耗次数。补齐后再次运行 process 会继续处理，无需 revise 或重置。',
                'reason':a.get('last_error'),'resume':'process','attempts_spent':False})
        if not a["selected"] or a["method"] not in {"generate", "repair"}:
            continue
        if a["status"] in {"awaiting_native", "awaiting_repair"}:
            notices.append({"asset_id": a["id"], "code": "host_image_action_required",
                "message": "该素材等待宿主生图/编辑，尚未完成。脚本无法判断宿主工具是否可用；执行 agent 必须检查工具并说明实际失败原因。",
                "on_host_failure": ["告知用户并保留待办", "稍后重试或上传已有设计/修复候选",
                                    "仅经用户明确选择后配置 API，不自动切换"]})
        elif a["status"] == "failed":
            notices.append({"asset_id": a["id"], "code": "image_processing_failed",
                "message": "生图或图像处理失败，尚未完成；请查看本地记录，不在聊天中发送密钥。"})
    return notices


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,"reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        result = run(parser().parse_args())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
