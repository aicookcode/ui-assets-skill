"""Shared release gate. A file existing is not evidence of visual fidelity."""
from workflow import validate_visual_checks
from surface import applies, validate_surface


def release_problems(asset, version):
    if not version:
        return ["no current output"]
    quality = version["quality"]
    problems = list(quality.get("issues", []))
    if applies(asset):
        try:
            validate_surface(asset)
        except ValueError as exc:
            problems.append(str(exc))
        if not quality.get("surface_edge_anchors"):
            problems.append("surface edge evidence missing; legacy output requires reprocessing")
        elif any(m.get("pixel_equal") is not True for m in quality["surface_edge_anchors"]):
            problems.append("surface source edge changed")
        if quality.get("visual_review", {}).get("verdict") != "passed":
            problems.append("decorative surface requires explicit corner/padding/content review")
    if quality.get("protected_pixels_equal") is False:
        problems.append("protected reference pixels changed")
    review = quality.get("visual_review", {})
    if review:
        try:
            validate_visual_checks({**asset, "analysis_findings": quality.get("analysis_findings"),
                                    "canvas_mapping":quality.get("canvas_mapping",{}),
                                    'comparison_policy':quality.get('comparison_policy',{}),
                                    'icon_policy':quality.get('icon_policy')},
                                   review.get("verdict"), review.get("note"), review.get("checks"))
        except ValueError:
            problems.append("visual checklist is missing or obsolete")
    if quality.get("reviewed_status") in {"issues", "unknown"}:
        problems.append("visual comparison has not passed")
    if quality.get("status") != "passed" and review.get("verdict") != "passed":
        problems.append("visual inspection required")
    if asset["format"] == "svg" and not quality.get("render_preview"):
        problems.append("SVG requires a rendered preview of the current vector")
    if asset['format']=='svg':
        comparison=quality.get('source_comparison')
        if not comparison:
            problems.append('SVG requires current source comparison evidence; legacy pass is insufficient')
        elif review and review.get('checks',{}).get('icon_fidelity',{}).get('comparison_sha256')!=comparison.get('sha256'):
            problems.append('SVG source comparison has not been acknowledged in the visual review')
    return problems


def repair_findings(version):
    quality = version["quality"]
    findings = list(quality.get("issues", []))
    review = quality.get("visual_review", {})
    findings += [key + ": " + value["note"] for key, value in review.get("checks", {}).items()
                 if value["verdict"] in {"issues", "unknown"}]
    return findings


def phase_for(assets):
    selected = [a for a in assets if a["selected"]]
    if any(a["status"] in {"repair_blocked", "failed"} for a in selected):
        return "REPAIR_BLOCKED"
    if any(a["status"] in {"pending", "processing", "awaiting_native", "awaiting_repair", "repair_pending"}
           for a in selected):
        return "ASSET_PROCESSING"
    if any(a['status']=='awaiting_input' for a in selected):
        return 'INPUT_PENDING'
    if any(a["status"] == "awaiting_visual_review" for a in selected):
        return "QUALITY_VALIDATION"
    return "RESULT_REVIEW" if selected else "ASSET_ANALYSIS"
