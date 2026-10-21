"""Behavioral tests use synthetic pixels; they never call a live image provider."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile
from unittest.mock import patch
from io import BytesIO
import base64
import os

from PIL import Image, ImageDraw

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import studio
from fixture_analysis import analyse_fixture,synthetic_icon_review
from imaging import load, remove_edge_background, svg_check
import providers
from workflow import REVIEW_CHECKS


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "project"
        self.image = self.base / "design.png"
        im = Image.new("RGBA", (120, 100), "white")
        draw = ImageDraw.Draw(im)
        draw.rectangle((10, 10, 39, 39), fill="#cc3377")
        draw.ellipse((70, 30, 99, 59), fill="#3399aa")
        im.save(self.image)
        self.call("init", "--mode", "B", "--sources", str(self.image))
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
        parsed = studio.parser().parse_args(["--project", str(self.root), *args])
        return studio.run(parsed)

    def state(self):
        return studio.read(self.root / "project.json")

    def spec(self, aid="icon-1", box=None, **kwargs):
        box = box or [10, 10, 40, 40]
        a = {"id": aid, "name": "素材", "page": "home", "category": "icon",
             "source": "source-1", "box": box, "method": "crop", "format": "png",
             "composition": {"role": "icon", "code_reproducible": False,
                             "decision": "Synthetic reference fixture for processing tests.", "embedded_ui": []}, **kwargs}
        if a["method"] == "repair" and "repair_regions" not in a:
            a["repair_regions"] = [[0, 0, box[2]-box[0], box[3]-box[1]]]
        if a["method"] == "generate":
            a["composition"]["fallback_reasons"] = {
                method: "Synthetic test explicitly exercises the generation backend."
                for method in ("reuse", "crop", "vector", "repair")}
        return a

    def manifest(self, specs):
        path = self.base / "manifest.json"
        studio.write(path, specs)
        return self.call("manifest", "--file", str(path))

    def ready(self, specs=None):
        self.manifest(specs or [self.spec()])
        self.call("approve-manifest")
        self.call("process")

    def test_crop_is_exact_and_repeat_process_keeps_version(self):
        self.ready()
        a = self.state()["assets"][0]
        output = load(self.root / studio.current(a)["file"])
        self.assertEqual(output.tobytes(), load(self.image).crop(a["box"]).tobytes())
        self.assertEqual(studio.current(a)["quality"]["status"], "passed")
        self.call("process")
        self.assertEqual(len(self.state()["assets"][0]["versions"]), 1)

    def test_blending_cannot_bypass_explicit_repair_regions(self):
        with self.assertRaisesRegex(ValueError,"explicit repair_regions"):
            self.manifest([self.spec(edge_blend=[{"edge":"top","pixels":4,"color":"#ffffff","pattern_free":True}])])

    def test_generation_blocked_until_each_manifest_approved(self):
        specs = [self.spec()]
        self.manifest(specs)
        with self.assertRaisesRegex(ValueError, "approved"):
            self.call("process")
        self.assertFalse((self.root / "versions").exists())
        self.call("approve-manifest")
        specs[0]["box"] = [11, 10, 40, 40]
        self.manifest(specs)
        with self.assertRaisesRegex(ValueError, "approved"):
            self.call("process")

    def test_mode_a_design_confirmation(self):
        self.root = self.base / "description"
        self.call("init", "--mode", "A")
        with self.assertRaisesRegex(ValueError, "confirmed"):
            self.call("scope", "--all")
        self.call("design", "--sources", str(self.image))
        self.assertEqual(self.state()["phase"], "DESIGN_PENDING")
        self.call("approve-design")
        self.call("scope", "--all")
        self.assertEqual(self.state()["phase"], "ASSET_ANALYSIS")

    def test_scope_enforces_page_region_and_category(self):
        scope = self.base / "scope.json"
        studio.write(scope, {"regions": [{"source": "source-1", "page": "home", "box": [0, 0, 50, 50], "categories": ["icon"]}]})
        self.call("scope", "--file", str(scope))
        for bad in (self.spec(box=[40, 40, 60, 60]), self.spec(page="other"), self.spec(category="background")):
            with self.assertRaisesRegex(ValueError, "scope"):
                self.manifest([bad])
        self.manifest([self.spec()])

    def test_manifest_revision_preserves_selection_and_completed_asset(self):
        first, second = self.spec(), self.spec("icon-2", [70, 30, 100, 60], selected=False)
        self.ready([first, second])
        self.call("accept", "--assets", "icon-1")
        second.pop("selected")
        third = self.spec("icon-3", [0, 0, 8, 8])
        self.manifest([first, second, third])
        state = self.state()
        self.assertFalse(state["assets"][1]["selected"])
        self.assertEqual(state["assets"][0]["acceptance"], "accepted")
        self.assertEqual(len(state["assets"][0]["versions"]), 1)
        self.assertEqual(state["assets"][2]["status"], "pending")

    def test_locked_asset_spec_requires_explicit_revision(self):
        self.ready()
        self.call("accept", "--assets", "icon-1")
        with self.assertRaisesRegex(ValueError, "locked"):
            self.manifest([self.spec(box=[9, 9, 40, 40])])
        # Deselection alone keeps accepted state and history.
        self.manifest([self.spec(selected=False)])
        self.assertEqual(self.state()["assets"][0]["acceptance"], "accepted")

    def test_partial_revision_and_restore_keep_other_asset_locked(self):
        self.ready([self.spec(), self.spec("icon-2", [70, 30, 100, 60])])
        self.call("accept", "--assets", "icon-1", "icon-2")
        before = deepcopy(self.state()["assets"][1])
        self.call("revise", "--assets", "icon-1", "--reason", "check again")
        self.call("process")
        state = self.state()
        self.assertEqual(state["assets"][1], before)
        self.assertEqual([v["number"] for v in state["assets"][0]["versions"]], [1, 2])
        self.call("restore", "--asset", "icon-1", "--version", "1")
        a = self.state()["assets"][0]
        self.assertEqual(a["current_version"], 1)
        self.assertEqual(a["acceptance"], "pending")
        self.assertEqual(len(a["versions"]), 2)

    def test_native_requests_are_real_pending_jobs(self):
        self.ready([self.spec(method="repair", occluded=True, prompt="remove UI")])
        state = self.state()
        self.assertEqual(state["assets"][0]["status"], "awaiting_native")
        self.assertEqual(state["assets"][0]["attempts"], 0)
        notices = self.call("status")["notices"]
        self.assertEqual(notices[0]["asset_id"], "icon-1")
        self.assertEqual(notices[0]["code"], "host_image_action_required")
        task = studio.read(self.root / "requests" / "icon-1.json")
        self.assertTrue(Path(task["reference"]).is_file())
        generated = self.base / "generated.png"
        load(self.image).crop((10, 10, 40, 40)).save(generated)
        self.call("fulfill", "--asset", "icon-1", "--file", str(generated))
        a = self.state()["assets"][0]
        self.assertEqual(studio.current(a)["quality"]["status"], "unknown")
        with self.assertRaisesRegex(ValueError, "quality"):
            self.call("accept", "--assets", "icon-1")
        self.call("visual-review", "--asset", "icon-1", "--verdict", "passed", "--note", "visible region compared")
        self.call("accept", "--assets", "icon-1")
        # Automatic status does not turn passed just because user/model reviewed it.
        self.assertEqual(studio.current(self.state()["assets"][0])["quality"]["status"], "unknown")

    def test_missing_input_waits_without_attempts_and_does_not_block_other_assets(self):
        self.ready([self.spec("bad", method="reuse", input_file=str(self.base / "missing.png")), self.spec()])
        state = self.state()
        self.assertEqual(state["assets"][0]["status"], "awaiting_input")
        self.assertEqual(state["assets"][0]["attempts"], 0)
        self.assertEqual(state["assets"][1]["status"], "ready")

        self.call("process")
        self.assertEqual(self.state()["assets"][0]["attempts"], 0)
        notice=self.call('status')['notices'][0]
        self.assertEqual(notice['code'],'input_not_ready')
        self.assertEqual(notice['resume'],'process')
        # Completing the already-approved file unblocks this same asset.
        before=deepcopy(self.state()['assets'][1])
        load(self.image).crop((10,10,40,40)).save(self.base/'missing.png')
        self.call('process')
        a=self.state()['assets'][0]
        self.assertEqual(a['attempts'],1)
        self.assertEqual(a['current_version'],1)
        self.assertIsNone(a['last_error'])
        self.assertEqual(self.state()['assets'][1],before)

    def test_missing_vector_input_field_is_not_a_generation_failure(self):
        self.ready([self.spec(method='vector',format='svg')])
        a=self.state()['assets'][0]
        self.assertEqual(a['status'],'awaiting_input')
        self.assertEqual(a['attempts'],0)
        self.assertEqual(a['candidate_history'],[])
        self.call('process')
        self.assertEqual(self.state()['assets'][0]['attempts'],0)

    def test_svg_file_appearing_at_approved_path_resumes_without_revise(self):
        file=self.base/'pending.svg'
        self.ready([self.spec(method='vector',format='svg',input_file=str(file))])
        before=self.state()['assets'][0]
        self.assertEqual(before['status'],'awaiting_input')
        self.assertEqual(before['attempts'],0)
        self.assertEqual(self.state()['phase'],'INPUT_PENDING')
        file.write_text('<svg viewBox="0 0 30 30"><rect width="30" height="30" fill="#cc3377"/></svg>')
        self.call('process')
        a=self.state()['assets'][0]
        self.assertEqual(a['attempts'],1)
        self.assertEqual(a['status'],'awaiting_visual_review')
        self.assertEqual(len(a['versions']),1)
        self.assertEqual(len(a['candidate_history']),1)
        self.assertNotIn('revise',[e['action'] for e in self.state()['events']])

    def test_invalid_svg_waits_and_valid_replacement_resumes(self):
        file=self.base/'invalid.svg';file.write_text('unfinished SVG')
        self.ready([self.spec(method='vector',format='svg',input_file=str(file))])
        self.assertEqual(self.state()['assets'][0]['attempts'],0)
        self.assertEqual(self.state()['assets'][0]['status'],'awaiting_input')
        file.write_text('<svg viewBox="0 0 30 30"><rect width="30" height="30" fill="#cc3377"/></svg>')
        self.call('process')
        self.assertEqual(self.state()['assets'][0]['attempts'],1)
        self.assertEqual(len(self.state()['assets'][0]['versions']),1)

    def test_missing_native_result_does_not_spend_attempt_and_can_resume(self):
        self.ready([self.spec(method='repair')])
        file=self.base/'not-yet-generated.png'
        with self.assertRaisesRegex(ValueError,'not ready'):
            self.call('fulfill','--asset','icon-1','--file',str(file))
        a=self.state()['assets'][0]
        self.assertEqual(a['attempts'],0)
        self.assertEqual(a['status'],'awaiting_native')
        load(self.image).crop((10,10,40,40)).save(file)
        self.call('fulfill','--asset','icon-1','--file',str(file))
        self.assertEqual(self.state()['assets'][0]['attempts'],1)
        self.assertEqual(len(self.state()['assets'][0]['versions']),1)

    def test_task_preparation_error_is_not_counted_or_retried(self):
        with patch('studio.request_for',side_effect=ValueError('fixture dependency missing')) as request:
            self.ready([self.spec(method='repair'),self.spec('other')])
        self.assertEqual(request.call_count,1)
        self.assertEqual(self.state()['assets'][0]['attempts'],0)
        self.assertEqual(self.state()['assets'][0]['status'],'awaiting_input')
        self.assertEqual(self.state()['assets'][1]['status'],'ready')
        self.call('process')
        self.assertEqual(self.state()['assets'][0]['status'],'awaiting_native')
        self.assertEqual(self.state()['assets'][0]['attempts'],0)

    def test_technical_transparency_error_cannot_be_accepted(self):
        self.ready([self.spec(transparent=True)])
        a = self.state()["assets"][0]
        self.assertEqual(studio.current(a)["quality"]["status"], "issues")
        with self.assertRaisesRegex(ValueError, "technical"):
            self.call("accept", "--assets", "icon-1", "--accept-differences")

    def test_transparent_trim_keeps_soft_shadow(self):
        resource = self.base / "transparent.png"
        im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
        im.putpixel((4, 6), (10, 20, 30, 1))
        im.putpixel((30, 32), (255, 0, 0, 255))
        im.save(resource)
        self.ready([self.spec(method="reuse", input_file=str(resource), transparent=True, trim=True)])
        a = self.state()["assets"][0]
        v = studio.current(a)
        self.assertEqual(v["trim_offset"], [4, 6])
        out = load(self.root / v["file"])
        self.assertEqual(out.size, (27, 27))
        self.assertEqual(out.getpixel((0, 0))[3], 1)

    def test_edge_color_removal_does_not_erase_enclosed_same_color(self):
        im = Image.new("RGBA", (9, 9), "white")
        draw = ImageDraw.Draw(im)
        draw.rectangle((2, 2, 6, 6), outline="black")
        result = remove_edge_background(im, "#ffffff")
        self.assertEqual(result.getpixel((0, 0))[3], 0)
        self.assertEqual(result.getpixel((4, 4))[3], 255)

    def test_distortion_is_rejected(self):
        self.ready([self.spec(size=[100, 30])])
        self.assertEqual(self.state()["assets"][0]["status"], "failed")
        self.assertIn("aspect ratio", self.state()["assets"][0]["last_error"])

    def test_nine_slice_reassembles_original_exactly(self):
        self.ready([self.spec(nine_slice=[5, 6, 7, 8])])
        a = self.state()["assets"][0]
        directory = (self.root / studio.current(a)["file"]).parent
        metadata = studio.read(directory / "nine-slice.json")
        result = Image.new("RGBA", (30, 30))
        for piece in metadata["slices"]:
            image = load(directory / "slices" / piece["file"])
            result.paste(image, tuple(piece["box"][:2]))
        self.assertEqual(result.tobytes(), load(self.root / studio.current(a)["file"]).tobytes())
        self.assertEqual(len(metadata["slices"]), 9)

    def test_delivery_excludes_unaccepted_files_and_zip_reads(self):
        self.ready([self.spec(), self.spec("pending", [70, 30, 100, 60])])
        self.call("accept", "--assets", "icon-1")
        output = self.call("deliver")
        self.assertEqual(output["assets"], 1)
        self.assertEqual(output["excluded"], 1)
        with ZipFile(output["zip"]) as z:
            self.assertIsNone(z.testzip())
            manifest = json.loads(z.read("manifest.json"))
            self.assertEqual([a["id"] for a in manifest["assets"]], ["icon-1"])
            self.assertTrue(z.read(manifest["assets"][0]["file"]).startswith(b"\x89PNG"))
            self.assertFalse(any("project.json" in n or "requests/" in n or "provider" in n for n in z.namelist()))

    def test_modified_output_is_rejected_before_delivery(self):
        self.ready()
        self.call("accept", "--assets", "icon-1")
        a = self.state()["assets"][0]
        (self.root / studio.current(a)["file"]).write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "modified"):
            self.call("deliver")

    def test_modified_baseline_cannot_generate(self):
        self.manifest([self.spec()])
        p = self.state()
        (self.root / p["sources"][0]["file"]).write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError, "baseline"):
            self.call("approve-manifest")

    def test_path_traversal_duplicate_id_and_occluded_crop_rejected(self):
        for specs in ([self.spec("../bad")], [self.spec(), self.spec()], [self.spec(occluded=True)]):
            with self.assertRaises(ValueError):
                self.manifest(specs)
        self.assertEqual(self.state()["manifest_revision"], 0)

    def test_svg_static_validation_and_visual_review_requirement(self):
        path = self.base / "icon.svg"
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30"><rect width="30" height="30" fill="#cc3377"/></svg>')
        self.ready([self.spec(method="vector", format="svg", input_file=str(path))])
        a = self.state()["assets"][0]
        self.assertEqual(studio.current(a)["quality"]["status"], "unknown")
        with self.assertRaisesRegex(ValueError, "quality gate"):
            self.call("accept", "--assets", "icon-1", "--accept-differences")
        preview = self.base / "render.png"
        Image.new("RGBA", (30,30), "#cc3377").save(preview)
        self.call("render-preview", "--asset", "icon-1", "--version", "1", "--file", str(preview),
                  "--vector-sha256", studio.digest(self.root / studio.current(a)["file"]))
        self.call("visual-review", "--asset", "icon-1", "--verdict", "passed", "--note", "Compared the rendered solid synthetic square to its exact source.")
        self.call("accept", "--assets", "icon-1")
        with self.assertRaisesRegex(ValueError, "locked"):
            self.call("visual-review", "--asset", "icon-1", "--verdict", "passed", "--note", "test")
        path.write_text('<svg viewBox="0 0 30 30"><script>alert(1)</script></svg>')
        with self.assertRaises(ValueError):
            svg_check(path)

    def failed_svg_pair(self):
        path = self.base / 'trial.svg'
        path.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 30"><rect width="30" height="30" fill="red"/></svg>')
        self.ready([self.spec(method='vector', format='svg', input_file=str(path))])
        preview = self.base / 'trial-render.png'
        Image.new('RGBA', (30,30), 'red').save(preview)
        for number in (1, 2):
            a = self.state()['assets'][0]
            if number == 2:
                task = studio.read(self.root / 'requests/icon-1.json')
                self.call('fulfill', '--asset', 'icon-1', '--file', str(path), '--request-id', task['request_id'])
                a = self.state()['assets'][0]
            self.call('render-preview', '--asset', 'icon-1', '--version', str(number), '--file', str(preview),
                      '--vector-sha256', studio.digest(self.root / studio.current(a)['file']))
            a = self.state()['assets'][0]
            checks = {k:{'verdict':'passed','note':'Controlled fixture geometry checked.'}
                      for k in (*REVIEW_CHECKS,'major_objects')}
            fidelity = synthetic_icon_review()
            fidelity.update(verdict='issues', remaining_differences=['Red output differs from pink source.'],
                            comparison_sha256=studio.current(a)['quality']['source_comparison']['sha256'])
            fidelity['features']['color'].update(verdict='issues', differences=['Red versus pink.'])
            checks['icon_fidelity'] = fidelity
            studio.write(self.base/'mismatch.json', checks)
            self.call('visual-review', '--asset', 'icon-1', '--verdict', 'issues',
                      '--note', 'Red rendered square differs from pink source square.',
                      '--checks-file', str(self.base/'mismatch.json'))

    def test_two_failed_svg_comparisons_enable_two_png_attempts_without_reapproval(self):
        self.failed_svg_pair()
        original = self.state()['assets'][0]
        self.manifest([self.spec(transparent=True, trim=True)])
        p = self.state()
        a = p['assets'][0]
        self.assertEqual(p['approved_revision'], p['manifest_revision'])
        self.assertEqual(a['attempts'], 0)
        self.assertEqual(a['png_fallback']['svg_versions'], [1,2])
        self.assertEqual(a['versions'], original['versions'])
        self.call('process')
        a = self.state()['assets'][0]
        self.assertEqual(a['attempts'], 1)
        self.assertEqual(len(a['versions']), 3)
        self.call('visual-review', '--asset', 'icon-1', '--verdict', 'issues', '--note', 'Controlled PNG edge issue.')
        task = studio.read(self.root/'requests/icon-1.json')
        png = self.base/'png-result.png'
        Image.new('RGBA', (30,30), '#cc3377').save(png)
        self.call('fulfill', '--asset', 'icon-1', '--file', str(png), '--request-id', task['request_id'])
        self.call('visual-review', '--asset', 'icon-1', '--verdict', 'issues', '--note', 'Controlled PNG issue remains.')
        a = self.state()['assets'][0]
        self.assertEqual(a['attempts'], 2)
        self.assertEqual(a['status'], 'repair_blocked')
        self.assertEqual(len(a['candidate_history']), 4)

    def test_png_extra_budget_rejects_missing_comparison_or_changed_geometry(self):
        self.failed_svg_pair()
        before = self.state()
        a = before['assets'][0]
        changed = self.spec(box=[9,9,40,40], transparent=True, trim=True, selected=True)
        with self.assertRaisesRegex(ValueError, 'same source'):
            studio.png_fallback(self.root, before, a, changed)
        a['versions'][0]['quality'].pop('visual_review')
        with self.assertRaisesRegex(ValueError, 'two unresolved'):
            studio.png_fallback(self.root, before, a, self.spec(transparent=True, trim=True, selected=True))

    def test_interrupted_processing_recovers_without_overwriting_orphan_version(self):
        self.manifest([self.spec()])
        self.call("approve-manifest")
        p = self.state()
        p["assets"][0]["status"] = "processing"
        studio.save(self.root, p)
        orphan = self.root / "versions" / "icon-1" / "v0001"
        orphan.mkdir(parents=True)
        (orphan / "original.txt").write_text("preserve")
        self.call("process")
        self.assertEqual(self.state()["assets"][0]["current_version"], 2)
        self.assertEqual((orphan / "original.txt").read_text(), "preserve")

    def test_deleted_asset_can_be_readded_without_losing_history(self):
        first, second = self.spec(), self.spec("icon-2", [70, 30, 100, 60])
        self.ready([first, second])
        self.manifest([second])
        self.assertIn("icon-1", self.state()["archived_assets"])
        self.manifest([first, second])
        self.assertEqual(len(self.state()["assets"][0]["versions"]), 1)
        self.assertEqual(self.state()["assets"][0]["current_version"], 1)

    def test_cli_error_exit_and_no_lock_left_after_failure(self):
        process = subprocess.run([sys.executable, str(SCRIPTS / "studio.py"), "--project", str(self.root), "process"],
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 2)
        self.assertIn("approved", json.loads(process.stderr)["error"])
        self.assertFalse((self.root / ".studio.lock").exists())

    def test_next_page_batch_preserves_deselected_earlier_page(self):
        first = self.spec()
        self.ready([first])
        self.call("accept", "--assets", "icon-1")
        scope = self.base / "next-page.json"
        studio.write(scope, {"regions": [{"source": "source-1", "page": "page-2", "box": [60, 0, 120, 100]}]})
        self.call("scope", "--file", str(scope))
        first["selected"] = False
        self.manifest([first, self.spec("icon-2", [70, 30, 100, 60], page="page-2")])
        self.call("approve-manifest")
        self.call("process")
        assets = self.state()["assets"]
        self.assertEqual(assets[0]["acceptance"], "accepted")
        self.assertEqual(len(assets[0]["versions"]), 1)
        self.assertEqual(assets[1]["status"], "ready")

    def test_api_gateway_contract_without_live_request(self):
        config = {"endpoint": "https://gateway.example/generate", "model": "test", "key_env": "TEST_UI_KEY",
                  "capabilities": ["reference_edit", "transparent"]}
        a = self.spec(method="repair", transparent=True)
        task = providers.request_for(self.state(), a, self.image)
        response = BytesIO(json.dumps({"image_base64": base64.b64encode(self.image.read_bytes()).decode()}).encode())
        with patch.dict(os.environ, {"TEST_UI_KEY": "private-test-key"}), patch.object(providers, "build_opener") as opener:
            opener.return_value.open.return_value = response
            data = providers.api_generate(config, task)
            request = opener.return_value.open.call_args.args[0]
            payload = json.loads(request.data)
            self.assertEqual(payload["box"], a["box"])
            self.assertNotIn("reference", payload)
            self.assertEqual(base64.b64decode(payload["reference_image_base64"]), self.image.read_bytes())
            self.assertNotIn("private-test-key", request.data.decode())
            self.assertEqual(data, self.image.read_bytes())

    def test_api_errors_do_not_expose_credentials_or_response(self):
        config = {"endpoint": "https://gateway.example/generate", "model": "test", "key_env": "TEST_UI_KEY", "capabilities": ["reference_edit"]}
        task = providers.request_for(self.state(), self.spec(method="repair"), self.image)
        with patch.dict(os.environ, {"TEST_UI_KEY": "private-test-key"}), patch.object(providers, "build_opener") as opener:
            opener.return_value.open.side_effect = RuntimeError("secret-response private-test-key")
            with self.assertRaises(ValueError) as caught:
                providers.api_generate(config, task)
            self.assertNotIn("private-test-key", str(caught.exception))
            self.assertNotIn("secret-response", str(caught.exception))
        task["transparent_background"] = True
        with self.assertRaisesRegex(ValueError, "capabilities"):
            providers.api_generate(config, task)

    def test_provider_failure_config_does_not_enter_delivery(self):
        self.manifest([self.spec(method="repair"), self.spec("crop-1", [70, 30, 100, 60])])
        self.call("approve-manifest")
        config = self.base / "provider.json"
        studio.write(config, {"backend": "api", "endpoint": "https://gateway.example/generate", "model": "test",
                            "key_env": "TEST_UI_KEY", "capabilities": ["reference_edit"], "max_attempts": 2})
        with patch("studio.api_generate", side_effect=ValueError("sensitive error")):
            self.call("process", "--provider", str(config))
        state = self.state()
        self.assertNotIn("sensitive error", json.dumps(state))
        self.assertEqual(state["assets"][0]["attempts"], 2)
        self.assertEqual(state["assets"][1]["status"], "ready")
        notices = self.call("status")["notices"]
        self.assertEqual([n["asset_id"] for n in notices], ["icon-1"])
        self.assertEqual(notices[0]["code"], "image_processing_failed")
        self.assertNotIn("sensitive error", json.dumps(notices))

    def test_masked_native_repair_persists_mask_and_protected_quality(self):
        self.ready([self.spec(method="repair",repair_regions=[[5,5,10,10]])])
        candidate=self.base/"candidate.png"
        Image.new("RGBA",(30,30),"red").save(candidate)
        self.call("fulfill","--asset","icon-1","--file",str(candidate))
        version=studio.current(self.state()["assets"][0])
        self.assertTrue(version["quality"]["protected_pixels_equal"])
        output=load(self.root/version["file"])
        self.assertEqual(output.getpixel((0,0)),(204,51,119,255))
        self.assertEqual(output.getpixel((7,7)),(255,0,0,255))
        self.assertTrue(any(Path(f["path"]).name=="repair-mask.png" for f in version["files"]))

    def test_masked_repair_rejects_wrong_canvas_and_unexpected_holes(self):
        self.ready([self.spec(method="repair",repair_regions=[[5,5,10,10]])])
        candidate=self.base/"candidate.png"
        Image.new("RGBA",(40,30),"red").save(candidate)
        with self.assertRaisesRegex(ValueError,"exact reference"):
            self.call("fulfill","--asset","icon-1","--file",str(candidate))
        self.assertEqual(self.state()["assets"][0]["status"],"awaiting_repair")
        Image.new("RGBA",(30,30),(0,0,0,0)).save(candidate)
        task = studio.read(self.root / "requests/icon-1.json")
        self.call("fulfill","--asset","icon-1","--file",str(candidate),"--request-id",task["request_id"])
        q=studio.current(self.state()["assets"][0])["quality"]
        self.assertIn("masked repair unexpectedly removed opacity",q["issues"])
        with self.assertRaisesRegex(ValueError,"technical"):
            self.call("accept","--assets","icon-1","--accept-differences")


if __name__ == "__main__":
    unittest.main()
