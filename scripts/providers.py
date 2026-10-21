"""Backend boundary. Native calls belong to the host; API is an explicit extension."""
import base64
import json
import os
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlparse


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("provider redirects are not permitted")


def request_for(project, asset, reference):
    return {
        "asset_id": asset["id"], "method": asset["method"],
        "reference": str(reference.resolve()), "box": asset["box"],
        "target_size": asset.get("size") or [asset["box"][2]-asset["box"][0], asset["box"][3]-asset["box"][1]],
        "format": asset["format"], "composition": asset.get("composition"),
        "alpha_policy": asset.get("alpha_policy", "inspect"),
        "transparent_background": asset.get("transparent", False),
        "prompt": asset.get("prompt", "Extract the specified asset, preserving the confirmed design."),
        "invariants": "Preserve overall composition, palette, major subjects, scale and placement. "
                      "Do not add or remove a major object such as a bouquet, tree, boat or building. "
                      "Individual petals, tiny speckles and minor texture variation are not rejection criteria. "
                      "Declared clipped corners may be completed and are excluded from content identity comparison. "
                      "Remove only declared UI/text and inspect repaired edges for seams.",
        "occluded": asset.get("occluded", False),
        "revision_feedback": asset.get("reason", "initial extraction"),
        "repair_regions": asset.get("repair_regions"),
        "fidelity_regions": asset.get("fidelity_regions", []),
        "surface_contract": asset.get("composition", {}).get("surface"),
        "analysis_findings": (project.get("analysis_audit", {}).get("rounds", [{}])[-1].get("findings", [])
                              if project.get("analysis_audit", {}).get("allowance") else []),
        "limitations": "Occluded pixels cannot be proven identical to the unknown original.",
    }


def api_generate(config, task):
    """Generic reference-image JSON contract; not a vendor-specific API claim."""
    if urlparse(config["endpoint"]).scheme != "https":
        raise ValueError("API endpoint must use HTTPS")
    required = {"reference_edit"}
    if task["transparent_background"]:
        required.add("transparent")
    if not required.issubset(set(config.get("capabilities", []))):
        raise ValueError("provider lacks required capabilities")
    key = os.environ.get(config.get("key_env", "UI_ASSETS_API_KEY"))
    if not key:
        raise ValueError("provider credential environment variable is missing")
    payload = {**task, "model": config["model"], "parameters": config.get("parameters", {})}
    payload.pop("reference")
    payload.pop("reference_crop", None)  # Local-only helper path is not provider input.
    payload["reference_image_base64"] = base64.b64encode(task_path_bytes(task)).decode("ascii")
    req = Request(config["endpoint"], json.dumps(payload).encode(),
                  {"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    # Response details are deliberately not logged (may contain credentials or private URLs).
    try:
        with build_opener(NoRedirect()).open(req, timeout=90) as response:
            body = response.read(40 * 1024 * 1024 + 1)
        if len(body) > 40 * 1024 * 1024:
            raise ValueError("provider response too large")
        return base64.b64decode(json.loads(body)["image_base64"], validate=True)
    except Exception:
        raise ValueError("provider request failed; inspect provider locally without exposing credentials") from None


def task_path_bytes(task):
    from pathlib import Path
    return Path(task["reference"]).read_bytes()
