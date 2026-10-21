import sys
import unittest
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageChops
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import canvas
from imaging import load,preserve_reference
from workflow import validate_visual_checks,REVIEW_CHECKS


class CanvasTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.crop=Path(self.tmp.name)/'source.png'
        self.ref=Image.new('RGBA',(60,20),'#faf7ef')
        ImageDraw.Draw(self.ref).rectangle((0,0,9,9),fill='green');self.ref.save(self.crop)
        self.task={'prompt':'Remove text only.'};canvas.prepare(self.task,self.crop)

    def test_wide_asset_uses_square_carrier_without_stretch_or_new_bottom_band(self):
        m=self.task['canvas_mapping'];native=load(m['file'])
        # Simulate a provider returning its own larger square canvas.
        result,evidence=canvas.normalize(native.resize((2048,2048),Image.Resampling.NEAREST),self.ref,m)
        self.assertEqual(result.size,self.ref.size)
        self.assertEqual(evidence['native_size'],[2048,2048])
        out,mask=preserve_reference(result,self.ref,[[30,5,40,10]])
        self.assertEqual(out.getpixel((0,0)),self.ref.getpixel((0,0)))
        self.assertEqual(out.getpixel((59,19)),self.ref.getpixel((59,19)))

    def test_rejects_carrier_reframe_padding_changes_and_hash_changes(self):
        m=self.task['canvas_mapping'];native=load(m['file'])
        with self.assertRaisesRegex(ValueError,'aspect changed'):
            canvas.normalize(native.resize((1200,800)),self.ref,m)
        ImageDraw.Draw(native).rectangle((0,0,500,500),fill='black')
        with self.assertRaisesRegex(ValueError,'padding changed'):canvas.normalize(native,self.ref,m)
        Path(m['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash changed'):
            canvas.normalize(Image.new('RGBA',(1024,1024)),self.ref,m)

    def test_legacy_uniform_resize_allows_rounding_but_not_aspect_distortion(self):
        result,evidence=canvas.normalize(self.ref.resize((601,200)),self.ref)
        self.assertEqual(result.size,(60,20))
        with self.assertRaisesRegex(ValueError,'never stretch'):
            canvas.normalize(self.ref.resize((600,300)),self.ref)

    def test_canvas_alignment_is_required_after_coordinate_mapping(self):
        a={'canvas_mapping':{'requires_visual_alignment_review':True}}
        checks={k:{'verdict':'passed','note':'Checked synthetic source.'} for k in REVIEW_CHECKS}
        with self.assertRaisesRegex(ValueError,'canvas_alignment'):
            validate_visual_checks(a,'passed','Compared exact artwork.',checks)

    def test_overall_content_policy_requires_major_objects_review(self):
        a={'comparison_policy':{'level':'overall-major-objects'}}
        checks={k:{'verdict':'passed','note':'Overall view matches; individual petals were not counted.'} for k in REVIEW_CHECKS}
        with self.assertRaisesRegex(ValueError,'major_objects'):
            validate_visual_checks(a,'passed','Compared source scene.',checks)
        checks['major_objects']={'verdict':'passed','note':'Same tree and boat, no added bouquet; completion corners excluded.'}
        self.assertIn('major_objects',validate_visual_checks(a,'passed','Compared source scene.',checks))


if __name__=='__main__':unittest.main()
