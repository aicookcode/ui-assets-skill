"""Retain attempts, and display explicitly failed diagnostics without releasing them."""
from pathlib import Path
from uuid import uuid4
import hashlib
import shutil
from PIL import Image, ImageDraw
from imaging import load, svg_check
from workflow import signature


def archive(root, asset, path, reference, spec):
    if not asset.get("generation_cycle"):
        asset["generation_cycle"] = uuid4().hex
    folder = root / "candidates" / asset["id"] / uuid4().hex
    folder.mkdir(parents=True)
    target = folder / ("candidate" + (Path(path).suffix if path else ".png"))
    if path:
        shutil.copyfile(path, target)
    else:
        reference.save(target)
    record = {"cycle": asset["generation_cycle"], "attempt": asset["attempts"],
              "file": str(target.relative_to(root)), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
              "spec_sha256": signature(spec), "version": None, "error": None}
    try:
        svg_check(target) if asset["format"] == "svg" else load(target)
        record["readable"] = True
    except (ValueError, OSError, SyntaxError):
        record["readable"] = False
    asset.setdefault("candidate_history", []).append(record)
    return record


def diagnostic(root, assets, selections, spec_for, problems_for, verify):
    failed = [a for a in assets if a["selected"] and a["status"] in {"repair_blocked", "failed"}]
    pending = [a for a in assets if a["selected"] and a["status"] not in {"ready", "accepted", "repair_blocked", "failed"}]
    if not failed or pending or any(a["attempts"] < 2 for a in failed):
        raise ValueError("failed-preview requires a settled batch with two exhausted attempts per failed asset")
    if (not isinstance(selections, list) or any(not isinstance(x, dict) for x in selections)
            or {x.get("id") for x in selections} != {a["id"] for a in failed} or len(selections) != len(failed)):
        raise ValueError("select exactly one best candidate per failed asset, with comparison reason")
    chosen = []
    for a in failed:
        selection = next(x for x in selections if x["id"] == a["id"])
        if not isinstance(selection.get("reason"), str) or not selection["reason"].strip():
            raise ValueError("best candidate selection requires an explicit comparison reason")
        if type(selection.get("attempt")) is not int:
            raise ValueError("best candidate attempt must be an integer")
        candidates = [c for c in a.get("candidate_history", []) if c["cycle"] == a.get("generation_cycle")
                      and c["spec_sha256"] == signature(spec_for(a)) and c["attempt"] == selection.get("attempt")]
        if len(candidates) != 1 or not candidates[0]["readable"]:
            raise ValueError("selected candidate is missing, unreadable or belongs to another specification/cycle")
        c = candidates[0]
        raw = root / c["file"]
        if hashlib.sha256(raw.read_bytes()).hexdigest() != c["sha256"]:
            raise ValueError("failed candidate hash changed")
        version = next((v for v in a["versions"] if v["number"] == c["version"]), None)
        if version:
            verify(a, version)
            raw = root / version["file"]
        findings = problems_for(a, version) if version else [c["error"] or "processing did not produce a valid version"]
        if a["format"] == "svg":
            render = version and version["quality"].get("render_preview")
            if not render:
                raise ValueError("failed SVG needs an actual rendered preview before diagnostic display")
            raw = root / render["file"]
            if hashlib.sha256(raw.read_bytes()).hexdigest() != render["sha256"]:
                raise ValueError("failed SVG render changed")
        chosen.append((a, c, selection, raw, findings))
    folder = root / "previews" / ("failed-" + uuid4().hex[:12])
    folder.mkdir(parents=True)
    report = {"kind":"failed_diagnostic", "passed":False, "can_export":False,
              "label":"NOT PASSED / NOT FOR EXPORT", "assets":[]}
    for a, c, selection, raw, findings in chosen:
        image = load(raw)
        view = Image.new("RGBA", (max(360, image.width), image.height+36), "#fff4ef")
        view.alpha_composite(image, (0,36))
        ImageDraw.Draw(view).text((8,10), "NOT PASSED - DO NOT EXPORT", fill="#b00020")
        out = folder / (a["id"] + "-not-passed.png")
        view.convert("RGB").save(out)
        report["assets"].append({"id":a["id"], "attempt":c["attempt"], "file":out.name,
            "reason":selection["reason"], "findings":findings, "can_export":False})
    return folder, report
