"""Checkbox manifest review, with revision-bound submissions and no web server."""
import base64
import hashlib
import json
from io import BytesIO
from pathlib import Path

from imaging import load


def review_token(project):
    # Scope/design changes invalidate the UI even if the manifest number is unchanged.
    state = {key: project.get(key) for key in
             ("project_id", "manifest_revision", "design_revision", "scope", "sources", "assets", "review_feedback")}
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_submission(project, data):
    if not isinstance(data, dict):
        raise ValueError("selection submission must be an object")
    if (data.get("project_id") != project["project_id"] or
            type(data.get("manifest_revision")) is not int or
            data["manifest_revision"] != project["manifest_revision"] or
            data.get("review_token") != review_token(project)):
        raise ValueError("stale selection or different project; reopen the current checkbox review")
    if data.get("action") != "confirm-selection":
        raise ValueError("selection requires explicit confirmation")
    selected = data.get("selected_ids")
    if not isinstance(selected, list) or any(not isinstance(i, str) for i in selected):
        raise ValueError("selected_ids must be a list of asset IDs")
    known = {a["id"] for a in project["assets"]}
    if len(set(selected)) != len(selected) or not set(selected).issubset(known):
        raise ValueError("unknown or duplicate selection IDs")
    feedback = data.get("feedback", "")
    if not isinstance(feedback, str) or len(feedback) > 10000:
        raise ValueError("feedback must be text of at most 10000 characters")
    feedback = feedback.strip()
    if not selected and not feedback:
        raise ValueError("select at least one asset or provide revision feedback")
    if project.get("review_feedback") and not feedback:
        raise ValueError("resolve review feedback and reimport the manifest before approval")
    return set(selected), feedback


def render_review(root, project, target):
    assets = []
    cache = {}
    for a in project["assets"]:
        s = next(s for s in project["sources"] if s["id"] == a["source"])
        path = (root / s["file"]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("review source escapes project directory")
        if a["source"] not in cache:
            cache[a["source"]] = load(path)
        im = cache[a["source"]].crop(a["box"])
        im.thumbnail((300, 200))
        buf = BytesIO()
        im.save(buf, format="PNG")
        assets.append({key: a.get(key) for key in
                       ("id", "name", "page", "category", "format", "method", "selected", "composition", "size", "box")}
                      | {"preview": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")})
    data = {"project_id": project["project_id"], "manifest_revision": project["manifest_revision"],
            "review_token": review_token(project), "assets": assets,
            "feedback": project.get("review_feedback", {}).get("text", "")}
    # Escaping '<' prevents asset names from ending a script element.
    payload = json.dumps(data, ensure_ascii=False).replace("<", r"\u003c")
    template = Path(__file__).with_name("review.html").read_text(encoding="utf-8")
    fragment = template.replace("__REVIEW_DATA__", payload)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                      '<meta name="viewport" content="width=device-width,initial-scale=1">'
                      '<title>素材选择审核</title><body>' + fragment + '</body></html>', encoding="utf-8")
    inline = target.with_name("review-inline.html")
    inline.write_text(fragment, encoding="utf-8")
    return target
