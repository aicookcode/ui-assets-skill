import sys,tempfile,unittest
from pathlib import Path
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from source_extent import inspect,require_complete


class SourceExtentTests(unittest.TestCase):
    def asset(self,box):
        return {'id':'icon','format':'png','box':box,'matte_background':'#faf5ea','composition':{'role':'icon'}}
    def source(self):
        im=Image.new('RGB',(50,40),'#faf5ea');d=ImageDraw.Draw(im)
        d.rectangle((12,10,37,27),fill='#b48b57');return im
    def test_right_roof_cut_is_blocked_even_when_output_would_be_tight(self):
        report=inspect(self.source(),self.asset([8,6,30,32]))
        self.assertEqual(report['crossing_edges'],['right'])
        with self.assertRaisesRegex(ValueError,'beyond crop on right'):require_complete(report)
    def test_all_four_cut_directions_are_checked(self):
        boxes={'left':[17,6,42,32],'top':[8,17,42,32],'right':[8,6,30,32],'bottom':[8,6,42,20]}
        for side,box in boxes.items():
            with self.subTest(side=side):self.assertIn(side,inspect(self.source(),self.asset(box))['crossing_edges'])
    def test_complete_source_box_with_observation_margin_is_allowed(self):
        report=inspect(self.source(),self.asset([8,6,42,32]))
        require_complete(report);self.assertFalse(report['crossing_edges'])
    def test_one_pixel_noise_does_not_claim_a_structural_cut(self):
        im=self.source();ImageDraw.Draw(im).line((29,5,30,5),fill='#b48b57')
        self.assertFalse(inspect(im,self.asset([8,2,30,8]))['crossing_edges'])
    def test_context_is_bound_and_saved_for_non_flat_svg_source(self):
        a=self.asset([8,6,42,32]);a.pop('matte_background');a['format']='svg'
        with tempfile.TemporaryDirectory() as folder:
            report=inspect(self.source(),a,folder)
            self.assertTrue(Path(report['context_file']).is_file())
            self.assertEqual(report['automatic_method'],'unavailable')
            self.assertEqual(len(report['context_rgb_sha256']),64)


if __name__=='__main__':unittest.main()
