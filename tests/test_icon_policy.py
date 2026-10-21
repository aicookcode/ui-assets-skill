import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import icon_policy
import analysis_loop
from fixture_analysis import synthetic_icon_policy,synthetic_icon_review,synthetic_svg_trial


class IconPolicyTests(unittest.TestCase):
    def test_ambiguous_internals_cannot_be_claimed_as_faithful_svg(self):
        asset={'format':'svg','composition':{'role':'icon'}}
        policy=synthetic_icon_policy(asset)
        policy.update(clarity='ambiguous',details_verifiable=False,uncertain_features=['calendar internal mark'])
        with self.assertRaisesRegex(ValueError,'ambiguous'):icon_policy.validate(asset,policy)
        policy.update(approach='conservative-vector',simplifications=['Omit unreadable microglyph; keep verified outer structure.'])
        icon_policy.validate(asset,policy)

    def test_unverifiable_shading_requires_raster_fallback(self):
        asset={'format':'svg','composition':{'role':'icon'}};policy=synthetic_icon_policy(asset)
        policy['appearance_verifiable']=False
        with self.assertRaisesRegex(ValueError,'PNG'):icon_policy.validate(asset,policy)
        asset['format']='png';policy['approach']='source-raster'
        policy['svg_trial']=synthetic_svg_trial()
        icon_policy.validate(asset,policy)

    def test_png_without_actual_failed_svg_is_rejected(self):
        asset={'format':'png','composition':{'role':'icon'}}
        policy=synthetic_icon_policy(asset)
        policy.pop('svg_trial')
        with self.assertRaisesRegex(ValueError,'failed SVG trial'):icon_policy.validate(asset,policy)

    def test_svg_fallback_cannot_use_passed_or_tampered_evidence(self):
        asset={'format':'png','composition':{'role':'icon'}}
        policy=synthetic_icon_policy(asset)
        policy['svg_trial']['verdict']='passed'
        with self.assertRaisesRegex(ValueError,'failed SVG trial'):icon_policy.validate(asset,policy)
        policy['svg_trial']=synthetic_svg_trial()
        policy['svg_trial']['render_file_sha256']='wrong'
        with self.assertRaisesRegex(ValueError,'hash mismatch'):icon_policy.validate(asset,policy)

    def test_template_does_not_guess_clarity_or_preapprove_svg(self):
        entry=analysis_loop.template([{'id':'x','source':'source-1','box':[0,0,20,20],
              'format':'svg','composition':{'role':'icon'}}])['x']
        self.assertIsNone(entry['icon_policy']['outline_verifiable'])
        self.assertEqual(entry['icon_policy']['clarity'],'unknown')

    def test_pass_cannot_keep_unresolved_difference(self):
        review=synthetic_icon_review();review['remaining_differences']=['Right page still has wrong shape.']
        with self.assertRaisesRegex(ValueError,'contradicts'):icon_policy.review({}, {'icon_fidelity':review})


if __name__=='__main__':unittest.main()
