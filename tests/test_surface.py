"""Prevent rounded container fragments and new padding, using synthetic pixels."""
import sys
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
from surface import validate_surface, measure_surface, complete_corners, corner_mask
from imaging import preserve_reference
from workflow import REVIEW_CHECKS, validate_visual_checks
from quality_flow import release_problems
import test_quality_gate as gates
import studio


def specimen():
    corners = {"top_left":[0,0,4,4], "top_right":[28,0,32,4],
               "bottom_left":[0,28,4,32], "bottom_right":[28,28,32,32]}
    return {"id":"asset", "name":"fixture", "source":"source-1", "page":"home",
            "category":"illustration", "format":"png", "method":"repair",
            "box":[0,0,32,32], "repair_regions":[[12,10,20,16], *corners.values()],
            "composition":{"role":"illustration", "code_reproducible":False,
                "decision":"Restore container-clipped corners, preserving the visible painting.",
                "embedded_ui":[], "surface":{"source_frame":"rounded-container",
                    "corner_content":"plain",
                    "output":"full-bleed", "corner_regions":corners,
                    "observation":"Rounded source has four pale outer wedges; painting reaches lower center.",
                    "edge_anchors":[[4,31,28,32]]}}}


class SurfaceTests(unittest.TestCase):
    def test_patterned_corners_keep_generated_pattern_and_match_boundary(self):
        import numpy as np
        a=specimen();a['composition']['surface'].update(corner_content='patterned',corner_fill='generated-blend')
        data=np.zeros((32,32,4),dtype=np.uint8)
        for y in range(32):
            for x in range(32):data[y,x]=[80+x*3,100+y*2,90+(x+y)%4*14,255]
        generated=Image.fromarray(data)
        original=data.copy();original[:,:,:3]+=5
        ref=Image.fromarray(original);mask=corner_mask(a,ref.size);ref.paste('black',(0,0),mask)
        composited=Image.composite(generated,ref,mask)
        out,_=complete_corners(composited,ref,a,generated)
        got=np.array(out);active=np.array(mask)>0
        self.assertLessEqual(int(np.abs(got[:,:,:3][active].astype(int)-original[:,:,:3][active]).max()),1)
        self.assertGreater(int(got[:,:,:3][active].max())-int(got[:,:,:3][active].min()),20)
        self.assertTrue(np.array_equal(got[~active],np.array(composited)[~active]))

    def test_patterned_corners_cannot_use_edge_pixel_extension(self):
        a=specimen();a['composition']['surface'].update(corner_content='patterned',corner_fill='edge-repeat')
        with self.assertRaisesRegex(ValueError,'generated-blend'):validate_surface(a)

    def test_patterned_corners_need_uncomposited_generated_pixels(self):
        a=specimen();a['composition']['surface'].update(corner_content='patterned',corner_fill='generated-blend')
        ref=Image.new('RGBA',(32,32),'green')
        with self.assertRaisesRegex(ValueError,'normalized generated artwork'):complete_corners(ref,ref,a)

    def test_corner_content_must_be_explicit(self):
        a=specimen();del a['composition']['surface']['corner_content']
        with self.assertRaisesRegex(ValueError,'corner_content'):validate_surface(a)

    def test_auto_completion_fills_only_declared_corners_and_preserves_main_art(self):
        a=specimen();ref=Image.new('RGBA',(32,32),'#82a15e')
        mask=corner_mask(a,ref.size)
        ref.paste('white',(0,0),mask)
        candidate=ref.copy()
        # Main subject must never become a source for corner edits or be changed.
        ImageDraw.Draw(candidate).rectangle((12,10,19,15),fill='red')
        out,excluded=complete_corners(candidate,ref,a)
        self.assertEqual(out.getpixel((0,0)),(130,161,94,255))
        self.assertEqual(out.getpixel((31,31)),(130,161,94,255))
        self.assertEqual(out.getpixel((14,12)),(255,0,0,255))
        self.assertEqual(excluded.tobytes(),mask.tobytes())
        for y in range(32):
            for x in range(32):
                if not mask.getpixel((x,y)):self.assertEqual(out.getpixel((x,y)),candidate.getpixel((x,y)))
        self.assertFalse(measure_surface(out,ref,a)[0])

    def test_missing_contract_is_not_silently_grandfathered(self):
        a=specimen();del a['composition']['surface']
        with self.assertRaisesRegex(ValueError, 'composition.surface'):validate_surface(a)

    def test_text_only_repair_cannot_remove_source_rounding(self):
        a=specimen();a['repair_regions']=[[12,10,20,16]]
        with self.assertRaisesRegex(ValueError, 'text-only'):validate_surface(a)

    def test_rounded_or_ink_mask_cannot_claim_full_corner_coverage(self):
        for mask in ([{'box':[0,0,32,32], 'radius':6}],
                     [{'box':[0,0,32,32], 'ink_threshold':0, 'dilate':0}]):
            a=specimen();a['repair_regions']=mask
            with self.assertRaisesRegex(ValueError, 'text-only'):
                validate_surface(a, Image.new('RGBA',(32,32),'white'))

    def test_valid_corner_plan_and_protected_bottom(self):
        a=specimen();ref=Image.new('RGBA',(32,32),'#faf7ef')
        ImageDraw.Draw(ref).rectangle((4,30,27,31),fill='#34765e')
        validate_surface(a,ref)
        candidate=Image.new('RGBA',ref.size,'#eeccaa')
        result,_=preserve_reference(candidate,ref,a['repair_regions'])
        issues, measurements=measure_surface(result,ref,a)
        self.assertFalse(issues);self.assertTrue(all(m['pixel_equal'] for m in measurements))
        self.assertEqual(result.getpixel((0,0)),candidate.getpixel((0,0)))
        self.assertEqual(result.getpixel((16,31)),ref.getpixel((16,31)))

    def test_corner_wedge_strips_preserve_visible_art_inside_corner_bounds(self):
        a=specimen();old=a['composition']['surface']['corner_regions']['top_left']
        strips=[[0,0,4,1],[0,1,2,2],[0,2,1,4]]
        a['composition']['surface']['corner_regions']['top_left']=strips
        a['repair_regions'].remove(old);a['repair_regions'].extend(strips)
        ref=Image.new('RGBA',(32,32),'green');validate_surface(a,ref)
        out,_=preserve_reference(Image.new('RGBA',ref.size,'red'),ref,a['repair_regions'])
        self.assertEqual(out.getpixel((0,0)),(255,0,0,255))
        self.assertEqual(out.getpixel((3,3)),ref.getpixel((3,3)))

    def test_bottom_only_card_fragment_does_not_repaint_original_top_corners(self):
        a=specimen()
        for name in ('top_left','top_right'):
            box=a['composition']['surface']['corner_regions'][name]
            a['repair_regions'].remove(box)
            a['composition']['surface']['corner_regions'][name]=None
        ref=Image.new('RGBA',(32,32),'green');validate_surface(a,ref)
        out,_=preserve_reference(Image.new('RGBA',ref.size,'red'),ref,a['repair_regions'])
        self.assertEqual(out.getpixel((0,0)),ref.getpixel((0,0)))
        self.assertEqual(out.getpixel((0,31)),(255,0,0,255))

    def test_same_size_generated_bottom_blank_is_rejected(self):
        a=specimen();ref=Image.new('RGBA',(32,32),'green');result=ref.copy()
        ImageDraw.Draw(result).rectangle((0,29,31,31),fill='#faf7ef')
        issues,_=measure_surface(result,ref,a)
        self.assertIn('added margin',issues[0])

    def test_anchor_cannot_hide_in_middle_or_leave_bottom_uncovered(self):
        for boxes in ([[8,8,20,20]], [[4,31,10,32]]):
            a=specimen();a['composition']['surface']['edge_anchors']=boxes
            with self.assertRaises(ValueError):validate_surface(a)

    def test_repair_cannot_erase_or_reconstruct_preserved_bottom(self):
        a=specimen();a['repair_regions'].append([0,30,32,32])
        with self.assertRaisesRegex(ValueError, 'overlaps source edge anchors'):validate_surface(a)

    def test_full_card_crop_or_trim_cannot_pass_as_clean_surface(self):
        for change in ({'method':'crop'}, {'trim':True}, {'size':[64,64]}):
            a=specimen();a.update(change)
            with self.assertRaises(ValueError):validate_surface(a)

    def test_generic_visual_pass_cannot_skip_corner_padding_evidence(self):
        a=specimen();checks={k:{'verdict':'passed','note':'Compared fixture.'}
            for k in (*REVIEW_CHECKS, 'composition','repair_boundary')}
        with self.assertRaisesRegex(ValueError, 'container_corners'):
            validate_visual_checks(a,'passed','Compared source and result.',checks)
        for k in ('container_corners','edge_padding','content_position'):
            checks[k]={'verdict':'passed','note':'Compared all four corners and original edge/artwork positions.'}
        validate_visual_checks(a,'passed','Compared source and result.',checks)

    def test_legacy_pass_without_contract_or_measurements_cannot_release(self):
        a=specimen();del a['composition']['surface']
        v={'quality':{'status':'passed','issues':[]}}
        problems=release_problems(a,v)
        self.assertTrue(any('composition.surface' in s for s in problems))
        self.assertTrue(any('evidence missing' in s for s in problems))


class SurfacePipelineTests(unittest.TestCase):
    setUp = gates.QualityGateTests.setUp
    call = gates.QualityGateTests.call
    state = gates.QualityGateTests.state
    asset = gates.QualityGateTests.asset
    visual = gates.QualityGateTests.visual
    task = gates.QualityGateTests.task

    def test_unselected_legacy_surface_can_be_retained_without_mutation(self):
        a=specimen();a['selected']=False;del a['composition']['surface']
        studio.write(self.base/'manifest.json',[a])
        self.call('manifest','--file',str(self.base/'manifest.json'))
        self.assertFalse(self.asset()['selected'])
    def test_real_manifest_rejects_text_only_corner_plan_before_processing(self):
        a=specimen();a['repair_regions']=[[12,10,20,16]]
        studio.write(self.base/'manifest.json',[a])
        with self.assertRaisesRegex(ValueError,'text-only'):
            self.call('manifest','--file',str(self.base/'manifest.json'))
        self.assertFalse((self.root/'requests/asset.json').exists())

    def test_surface_review_blocks_preview_accept_and_zip_until_complete(self):
        a=specimen();studio.write(self.base/'manifest.json',[a])
        self.call('manifest','--file',str(self.base/'manifest.json'))
        self.call('approve-manifest');self.call('process')
        candidate=self.base/'candidate.png';Image.new('RGBA',(32,32),'#eeccaa').save(candidate)
        self.call('fulfill','--asset','asset','--file',str(candidate))
        with self.assertRaisesRegex(ValueError,'container_corners'):self.visual()
        for command in (('preview',),('accept','--assets','asset'),('deliver',)):
            with self.assertRaises(ValueError):self.call(*command)
        checks={k:{'verdict':'passed','note':'Compared synthetic reference with four repaired corners and unchanged scene bottom.'}
            for k in (*REVIEW_CHECKS,'composition','repair_boundary','container_corners','edge_padding','content_position','major_objects')}
        studio.write(self.base/'surface-checks.json',checks)
        self.call('visual-review','--asset','asset','--verdict','passed','--note','Checked rendered corner and edge evidence.',
            '--checks-file',str(self.base/'surface-checks.json'))
        self.call('preview');self.call('accept','--assets','asset')
        self.assertTrue(Path(self.call('deliver')['zip']).is_file())


if __name__=='__main__':unittest.main()
