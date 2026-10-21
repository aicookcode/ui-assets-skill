"""Regressions for the extraction failure and real checkbox submission protocol."""
from copy import deepcopy
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import studio
from fixture_analysis import analyse_fixture,synthetic_icon_review
from review import review_token, validate_submission
from imaging import qa
from workflow import REVIEW_CHECKS


class ReviewWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.source = self.base / "source.png"
        Image.new("RGBA", (100, 120), "#faf7ef").save(self.source)
        self.call("init", "--mode", "B", "--sources", str(self.source))
        self.call("scope", "--all")

    def call(self, *args):
        args = list(args)
        if args[0] == 'manifest':
            analyse_fixture(self.root, Path(args[args.index('--file')+1]))
        if args[0] == "fulfill" and "--reference-sha256" not in args:
            aid = args[args.index("--asset")+1]
            args += ["--reference-sha256", studio.read(self.root/"requests"/(aid+".json"))["reference_crop_sha256"]]
        actions = {"scope", "approve-design", "approve-manifest", "select", "feedback",
                   "accept", "revise", "restore", "reject", "review-ui"}
        if args[0] in actions or (args[0] == "process" and "--provider" in args):
            if "--user-text" not in args:
                args += ["--user-text", "Explicit instruction for this controlled test.", "--message-ref", "test-fixture-message"]
        if args[0] == "visual-review" and "--checks-file" not in args:
            checks = {k: {"verdict": args[args.index("--verdict")+1], "note": "Compared synthetic reference and candidate."}
                      for k in (*REVIEW_CHECKS, "composition", "repair_boundary",'major_objects')}
            checks['icon_fidelity']=synthetic_icon_review()
            aid=args[args.index('--asset')+1]
            asset=next(a for a in self.state()['assets'] if a['id']==aid)
            evidence=studio.current(asset)['quality'].get('source_comparison')
            if evidence:checks['icon_fidelity']['comparison_sha256']=evidence['sha256']
            studio.write(self.base/"checks.json", checks)
            args += ["--checks-file", str(self.base/"checks.json")]
        return studio.run(studio.parser().parse_args(["--project", str(self.root), *args]))

    def state(self):
        return studio.read(self.root/"project.json")

    def asset(self, aid="sprout", **changes):
        return {"id": aid, "name": "嫩芽", "page": "quiz", "category": "icon",
                "source": "source-1", "box": [10, 10, 40, 40], "format": "png", "method": "crop",
                "composition": {"role": "icon", "code_reproducible": False,
                                "decision": "Preserve the original hand-painted shape.", "embedded_ui": []}, **changes}

    def manifest(self, assets):
        studio.write(self.base/"manifest.json", assets)
        return self.call("manifest", "--file", str(self.base/"manifest.json"))

    def submission(self, ids=None, feedback=""):
        p = self.state()
        return {"action": "confirm-selection", "project_id": p["project_id"],
                "manifest_revision": p["manifest_revision"], "review_token": review_token(p),
                "selected_ids": ids if ids is not None else [a["id"] for a in p["assets"]], "feedback": feedback,
                "confirmation": {"source": "user_message", "text": "Explicit checkbox confirmation for test.", "message_ref": "test-selection-message"}}

    def submit(self, data):
        studio.write(self.base/"selection.json", data)
        return self.call("import-selection", "--file", str(self.base/"selection.json"))

    def test_missing_analysis_rejected_without_state_change(self):
        a = self.asset(); a.pop("composition")
        before = self.state()
        with self.assertRaisesRegex(ValueError, "composition analysis"):
            self.manifest([a])
        self.assertEqual(before, self.state())

    def test_legacy_project_readable_but_cannot_skip_analysis(self):
        p = self.state(); p["schema_version"] = 1; studio.save(self.root, p)
        a = self.asset(); a.pop("composition"); self.manifest([a])
        self.call("status")
        with self.assertRaisesRegex(ValueError, "composition analysis"):
            self.call("approve-manifest")
        p = self.state(); p["approved_revision"] = p["manifest_revision"]; studio.save(self.root, p)
        with self.assertRaisesRegex(ValueError, "composition analysis"):
            self.call("process")
        self.assertFalse((self.root/"requests").exists())

    def test_simple_vector_cannot_be_sent_to_generation(self):
        a = self.asset(method="generate"); a["composition"]["code_reproducible"] = True
        with self.assertRaisesRegex(ValueError, "code-reproducible"):
            self.manifest([a])

    def test_generation_requires_reasons_for_rejecting_deterministic_methods(self):
        a = self.asset(method="generate")
        with self.assertRaisesRegex(ValueError, "fallback_reasons"):
            self.manifest([a])
        a["composition"]["fallback_reasons"] = {m: "Unavailable for this reference." for m in ("reuse", "crop", "vector", "repair")}
        self.manifest([a])

    def test_background_fragment_cannot_be_full_base(self):
        a = self.asset(category="background")
        a["composition"] = {"role": "background", "code_reproducible": False,
            "decision": "Bottom scenery with continuous plain upper region.", "embedded_ui": [],
            "layout_independent": True, "background": {"pattern": "bottom-only", "strategy": "full-base",
            "scale": "uniform", "anchor": "bottom", "base_color": "#faf7ef", "canvas_box": [0,0,100,120]}}
        with self.assertRaisesRegex(ValueError, "complete page"):
            self.manifest([a])
        a["box"] = [0,0,100,120]
        a["composition"]["surface"] = {"source_frame":"none", "output":"full-bleed",
            "source_ui":{"observation":"Controlled synthetic source has no UI modules.","regions":[]},
            "observation":"Unframed synthetic scenery with an unchanged bottom edge.",
            "edge_anchors":[[0,119,100,120]]}
        self.manifest([a])

    def test_top_crop_cannot_redefine_the_page_canvas(self):
        a = self.asset(category="background")
        a['box'] = [0,0,100,30]
        a['composition'] = {'role':'background','code_reproducible':False,
            'decision':'Top art with a lower continuous page base.','embedded_ui':[],
            'layout_independent':True,'background':{'pattern':'top-only','strategy':'full-base',
            'scale':'uniform','anchor':'top','base_color':'#faf7ef','canvas_box':[0,0,100,30]}}
        with self.assertRaisesRegex(ValueError, 'approved page scope'):
            self.manifest([a])

    def test_repair_needs_mask_and_request_has_actual_crop(self):
        a = self.asset(method="repair")
        with self.assertRaisesRegex(ValueError, "explicit repair_regions"):
            self.manifest([a])
        a["repair_regions"] = [[2,2,8,8]]
        self.manifest([a]); self.call("approve-manifest"); self.call("process")
        task = studio.read(self.root/"requests/sprout.json")
        self.assertEqual(task["target_size"], [30,30])
        self.assertEqual(task["format"], "png")
        self.assertEqual(task["composition"], a["composition"])
        with Image.open(task["reference_crop"]) as im:
            self.assertEqual(im.size, (30,30))

    def test_checkbox_submission_selects_only_requested_ids(self):
        self.manifest([self.asset(), self.asset("mountain", selected=False)])
        data = self.submission(["mountain"]); self.submit(data)
        p = self.state()
        self.assertEqual([a["id"] for a in p["assets"] if a["selected"]], ["mountain"])
        self.assertEqual(p["approved_revision"], p["manifest_revision"])
        self.assertFalse((self.root/"versions").exists())  # Approval never starts production by itself.
        with self.assertRaisesRegex(ValueError, "pending manifest"):
            self.submit(data)

    def test_feedback_blocks_generation_until_changed_manifest_reviewed(self):
        a = self.asset(); self.manifest([a])
        self.submit(self.submission(feedback="缩小边界并保留书页线条"))
        self.assertIn("review_feedback", self.state())
        with self.assertRaisesRegex(ValueError, "feedback"):
            self.call("approve-manifest")
        with self.assertRaisesRegex(ValueError, "feedback"):
            self.submit(self.submission())
        with self.assertRaisesRegex(ValueError, "changed manifest"):
            self.manifest([a])
        a["box"] = [12,12,40,40]; self.manifest([a]); self.submit(self.submission())
        self.assertEqual(self.state()["phase"], "ASSET_PROCESSING")

    def test_stale_revision_scope_project_and_unknown_ids_are_rejected(self):
        self.manifest([self.asset()]); data = self.submission(); before = self.state()
        for update in ({"project_id":"other"}, {"manifest_revision":0}, {"review_token":"wrong"},
                       {"selected_ids":["missing"]}, {"selected_ids":["sprout","sprout"]},
                       {"action":"toggle"}, {"selected_ids":[]}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.submit(data | update)
            self.assertEqual(self.state(), before)
        self.call("scope", "--all")
        with self.assertRaisesRegex(ValueError, "pending manifest"):
            self.submit(data)
        self.manifest([self.asset()])
        with self.assertRaisesRegex(ValueError, "stale"):
            self.submit(data)

    def test_review_is_self_contained_and_script_safe(self):
        name = '</script><img src=x onerror=alert(1)>'
        self.manifest([self.asset(name=name), self.asset("mountain", selected=False)])
        result = self.call("review-ui")
        html = Path(result["file"]).read_text(encoding="utf-8")
        self.assertNotIn(name, html)
        match = re.search(r'<script type="application/json" id="asset-review-data">(.*?)</script>', html, re.S)
        data = json.loads(match[1])
        self.assertEqual(data["assets"][0]["name"], name)
        self.assertEqual(len(data["assets"]), 2)
        self.assertTrue(data["assets"][0]["preview"].startswith("data:image/png;base64,"))
        self.assertIn("check.type='checkbox'", html)
        self.assertIn("sendFollowUpMessage", html)
        self.assertIn('asset-selection.json', html)
        self.assertTrue(Path(result["inline_file"]).is_file())

    def test_internal_transparent_holes_are_not_automatically_accepted(self):
        im = Image.new("RGBA", (80,80), (0,0,0,0)); d = ImageDraw.Draw(im)
        d.ellipse((5,5,74,74), fill="#347359"); d.ellipse((20,20,30,30), fill=(0,0,0,0))
        path = self.base/"holes.png"; im.save(path)
        q = qa(path, {"method":"generate", "transparent":True, "alpha_policy":"solid"}, im)
        self.assertEqual(q["status"], "issues")
        self.assertEqual(q["internal_alpha_holes"], 1)
        q = qa(path, {"method":"crop", "transparent":True}, im)
        self.assertEqual(q["status"], "unknown")  # Hollow icons may be legitimate: review, do not erase.


if __name__ == "__main__":
    unittest.main()
