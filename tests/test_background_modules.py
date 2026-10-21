"""Regress the real failure: round masks preserve old module exteriors."""
import sys, unittest
from pathlib import Path
from PIL import Image, ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from surface import validate_surface, measure_background_ui
from workflow import REVIEW_CHECKS, validate_visual_checks


def asset():
    return {'id':'bg','box':[0,0,40,60],'format':'png','method':'repair',
        'repair_regions':[[0,20,40,60]],'composition':{'role':'background',
        'surface':{'source_frame':'none','output':'full-bleed',
            'observation':'Page corners are square but an internal card reaches the bottom.',
            'edge_anchors':[[0,0,40,1]],
            'source_ui':{'observation':'Card including exterior corners and shadow occupies the lower field.',
                'regions':[{'kind':'module','box':[0,20,40,60]}]}}}}


class BackgroundModuleTests(unittest.TestCase):
    def test_old_rounded_module_mask_is_rejected(self):
        a=asset();a['repair_regions']=[{'box':[0,20,40,60],'radius':8}]
        with self.assertRaisesRegex(ValueError,'leaves source UI protected'):validate_surface(a)

    def test_text_only_mask_is_rejected(self):
        a=asset();a['repair_regions']=[{'box':[0,20,40,60],'ink_threshold':80}]
        with self.assertRaisesRegex(ValueError,'leaves source UI protected'):
            validate_surface(a,Image.new('RGBA',(40,60),'white'))

    def test_unobserved_legacy_background_is_rejected(self):
        a=asset();del a['composition']['surface']['source_ui']
        with self.assertRaisesRegex(ValueError,'source_ui'):validate_surface(a)

    def test_entire_lower_field_can_be_rebuilt_without_protecting_old_bottom(self):
        validate_surface(asset())

    def test_shadow_outside_repair_is_rejected(self):
        a=asset();a['repair_regions']=[[2,22,38,58]]
        with self.assertRaisesRegex(ValueError,'full footprint'):validate_surface(a)

    def test_ui_pixels_cannot_be_an_unchanged_edge_anchor(self):
        a=asset();a['composition']['surface']['edge_anchors']=[[0,59,40,60]]
        with self.assertRaisesRegex(ValueError,'source UI'):validate_surface(a)

    def test_known_footprint_not_changed_is_rejected_even_with_valid_mask(self):
        a=asset();ref=Image.new('RGBA',(40,60),'#faf5ef')
        ImageDraw.Draw(ref).rounded_rectangle((1,21,38,59),radius=8,fill='white')
        issues,evidence=measure_background_ui(ref.copy(),ref,a)
        self.assertTrue(issues);self.assertTrue(evidence[0]['source_ui_unchanged'])
        out=ref.copy();out.paste('#faf5ef',(0,20,40,60))
        self.assertFalse(measure_background_ui(out,ref,a)[0])

    def test_general_visual_pass_needs_separate_bare_background_review(self):
        a=asset();checks={k:{'verdict':'passed','note':'Synthetic source compared.'}
            for k in (*REVIEW_CHECKS,'composition','repair_boundary','container_corners','edge_padding','content_position')}
        with self.assertRaisesRegex(ValueError,'module_residue'):
            validate_visual_checks(a,'passed','Compared.',checks)
        checks['module_residue']={'verdict':'issues','note':'The lower rounded boundary remains.'}
        with self.assertRaisesRegex(ValueError,'cannot override'):
            validate_visual_checks(a,'passed','Compared.',checks)

    def test_deterministic_plain_extension_must_fully_cover_ui(self):
        a=asset();a['repair_regions']=[]
        a['background_join']={'start_y':5,'transition':10,'pattern_free':True,'base_color':'#faf5ef'}
        validate_surface(a)
        a['background_join']['transition']=20
        with self.assertRaisesRegex(ValueError,'leaves source UI protected'):validate_surface(a)


if __name__=='__main__':unittest.main()
