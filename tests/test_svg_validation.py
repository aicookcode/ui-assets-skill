import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw, ImageChops
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import svg_validation as sv
from fixture_analysis import synthetic_icon_review

class SvgValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        Image.new('RGBA',(20,20),'#cc7733').save(self.root/'source.png')
        Image.new('RGBA',(16,16),'#cc7733').save(self.root/'render.png')
        (self.root/'icon.svg').write_text('<svg viewBox="0 0 16 16"><rect width="16" height="16" fill="#cc7733"/></svg>')
        self.a={'id':'x','source':'source-1','box':[2,2,18,18]}
        self.s={'id':'source-1','file':'source.png','sha256':sv.digest(self.root/'source.png')}
        self.v={'number':1,'file':'icon.svg','quality':{'render_preview':{'file':'render.png'}}}
        self.e=sv.build(self.root,self.a,self.v,self.s)
        self.v['quality']['source_comparison']=self.e
        self.checks={'icon_fidelity':synthetic_icon_review()}
        self.checks['icon_fidelity']['comparison_sha256']=self.e['sha256']

    def test_bound_real_crop_render_and_comparison(self):
        sv.validate(self.root,self.a,self.v,self.s,self.checks)
        with Image.open(self.root/self.e['source_crop']) as image:
            self.assertEqual(image.size,(16,16))
        self.assertTrue((self.root/self.e['file']).is_file())

    def test_cannot_skip_view_acknowledgment(self):
        del self.checks['icon_fidelity']['comparison_sha256']
        with self.assertRaisesRegex(ValueError,'acknowledge'):sv.validate(self.root,self.a,self.v,self.s,self.checks)

    def test_high_resolution_render_is_area_downsampled_without_jagged_comparison(self):
        render=Image.new('RGBA',(256,256))
        ImageDraw.Draw(render).ellipse((32,32,224,224),fill='#cc7733')
        render.save(self.root/'render.png')
        evidence=sv.build(self.root,self.a,self.v,self.s)
        native=render.resize((16,16),Image.Resampling.LANCZOS)
        expected=Image.new('RGBA',(16,16),'#faf8f3');expected.alpha_composite(native)
        with Image.open(self.root/evidence['file']) as board:
            # Shared frame is 28px, with the target at its original (6,6) inset.
            left=board.width//2+(board.width//2-28)//2+6
            actual=board.crop((left,38,left+16,54)).convert('RGB')
        difference=ImageChops.difference(expected.convert('RGB'),actual)
        self.assertLessEqual(max(hi for lo,hi in difference.getextrema()),3)

    def test_changed_render_source_and_comparison_are_rejected(self):
        for file in ['source.png','render.png',self.e['source_crop'],self.e['file']]:
            with self.subTest(file=file):
                path=self.root/file;old=path.read_bytes();path.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'stale'):sv.validate(self.root,self.a,self.v,self.s,self.checks)
                path.write_bytes(old)

    def test_other_source_box_cannot_reuse_evidence(self):
        self.a['box']=[0,0,16,16]
        with self.assertRaisesRegex(ValueError,'stale'):sv.validate(self.root,self.a,self.v,self.s,self.checks)

    def test_changed_version_or_alignment_cannot_reuse_evidence(self):
        self.v['number']=2
        with self.assertRaisesRegex(ValueError,'stale'):sv.validate(self.root,self.a,self.v,self.s,self.checks)
        self.v['number']=1;self.v['trim_offset']=[1,0]
        with self.assertRaisesRegex(ValueError,'stale'):sv.validate(self.root,self.a,self.v,self.s,self.checks)

    def test_features_and_second_review_are_required(self):
        review=synthetic_icon_review();del review['features']['layers']
        with self.assertRaisesRegex(ValueError,'layers'):sv.feature_review(review)
        review=synthetic_icon_review();review['independent_review']['verdict']='unknown'
        with self.assertRaisesRegex(ValueError,'independent'):sv.feature_review(review)

    def test_generic_pass_cannot_override_specific_shape_issue(self):
        review=synthetic_icon_review()
        review['features']['outline'].update(verdict='issues',differences=['Incorrect cup silhouette'])
        with self.assertRaisesRegex(ValueError,'cannot override'):sv.feature_review(review)

if __name__=='__main__':unittest.main()
