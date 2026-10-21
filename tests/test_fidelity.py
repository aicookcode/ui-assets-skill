from pathlib import Path
import sys
import tempfile
import unittest

from PIL import Image,ImageDraw,ImageChops

SCRIPTS=Path(__file__).resolve().parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
from imaging import preserve_reference,repair_mask,extract_matte,qa,transform
from compare_render import compare


class FidelityTests(unittest.TestCase):
    def test_text_window_matches_source_paper_without_recreating_glyphs(self):
        clean=Image.new('RGBA',(80,50))
        for y in range(50):
            for x in range(80):clean.putpixel((x,y),(220+x//4,215+y//3,200+x//5,255))
        ref=clean.copy();draw=ImageDraw.Draw(ref)
        draw.rectangle((20,15,24,34),fill='black');draw.rectangle((35,15,39,34),fill='black')
        # The generated result is clean but has a shifted paper tint.
        import numpy as np
        values=np.array(clean,dtype='int16');values[:,:,:3]+=np.array([9,-5,14])
        candidate=Image.fromarray(values.astype('uint8'))
        evidence=[]
        out,mask=preserve_reference(candidate,ref,[{'box':[12,10,55,40],'blend':'boundary'}],evidence)
        delta=ImageChops.difference(out,clean)
        self.assertLessEqual(max(pair[1] for pair in delta.getextrema()),1)
        self.assertEqual(out.getpixel((1,1)),ref.getpixel((1,1)))
        self.assertTrue(evidence);self.assertLess(evidence[0]['residual'],.05)

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_generated_candidate_cannot_change_protected_pixels(self):
        ref=Image.new("RGBA",(40,30),"#f7efdc")
        ImageDraw.Draw(ref).ellipse((2,4,14,20),fill="#328177")
        candidate=Image.new("RGBA",ref.size,"red")
        result,mask=preserve_reference(candidate,ref,[[20,10,30,20]])
        protected=ImageChops.invert(mask)
        diff=ImageChops.difference(ref,result)
        self.assertIsNone(ImageChops.multiply(diff.getchannel("R"),protected).getbbox())
        self.assertEqual(result.getpixel((24,15)),(255,0,0,255))
        self.assertEqual(result.getpixel((5,10)),ref.getpixel((5,10)))

    def test_ink_mask_preserves_paper_between_glyphs(self):
        ref=Image.new("RGBA",(30,20),"#fefbf7")
        draw=ImageDraw.Draw(ref);draw.rectangle((4,5,6,12),fill="black");draw.rectangle((18,5,20,12),fill="black")
        candidate=Image.new("RGBA",ref.size,"#eee8dd")
        result,mask=preserve_reference(candidate,ref,[{"box":[1,1,28,18],"ink_threshold":185,"dilate":1}])
        self.assertEqual(mask.getpixel((12,8)),0)
        self.assertEqual(result.getpixel((12,8)),ref.getpixel((12,8)))
        self.assertEqual(mask.getpixel((5,8)),255)

    def test_overlapping_glyph_window_does_not_erase_repair_band(self):
        ref=Image.new("RGBA",(40,30),"#fefbf7")
        ImageDraw.Draw(ref).rectangle((10,12,12,17),fill="black")
        mask=repair_mask(ref.size,[[0,0,40,15],{"box":[5,10,30,22],"ink_threshold":185,"dilate":0}],ref)
        self.assertEqual(mask.getpixel((20,12)),255)
        self.assertEqual(mask.getpixel((11,17)),255)
        self.assertEqual(mask.getpixel((20,20)),0)

    def test_light_ink_mask_only_removes_text_on_dark_button(self):
        ref=Image.new("RGBA",(30,20),"#347a5b")
        ImageDraw.Draw(ref).rectangle((5,5,8,12),fill="white")
        mask=repair_mask(ref.size,[{"box":[1,1,28,18],"ink_mode":"light","ink_threshold":190,"dilate":0}],ref)
        self.assertEqual(mask.getpixel((15,8)),0)
        self.assertEqual(mask.getpixel((6,8)),255)

    def test_mask_wrong_canvas_rejected_without_warp(self):
        with self.assertRaisesRegex(ValueError,"exact reference"):
            preserve_reference(Image.new("RGBA",(40,40)),Image.new("RGBA",(40,30)),[[0,0,10,10]])

    def test_rounded_mask_protects_visible_paper_at_card_corners(self):
        mask=repair_mask((30,20),[{"box":[2,2,28,18],"radius":6}])
        self.assertEqual(mask.getpixel((2,2)),0)
        self.assertEqual(mask.getpixel((10,8)),255)

    def test_generation_aspect_drift_is_a_technical_error(self):
        file=self.root/"wrong.png"
        Image.new("RGBA",(40,30),"white").save(file)
        quality=qa(file,{"method":"generate"},Image.new("RGBA",(30,30),"white"))
        self.assertEqual(quality["status"],"issues")
        self.assertIn("output aspect ratio differs from reference",quality["issues"])

    def test_flat_matte_preserves_opaque_color_and_roundtrips(self):
        bg=(254,251,247)
        ref=Image.new("RGBA",(15,15),bg+(255,))
        ImageDraw.Draw(ref).rectangle((3,3,11,11),fill=(49,115,90,255))
        ref.putpixel((2,6),(152,183,169,255))
        output=extract_matte(ref,"#fefbf7")
        self.assertEqual(output.getpixel((7,7)),(49,115,90,255))
        self.assertEqual(output.getpixel((0,0))[3],0)
        self.assertLess(output.getpixel((2,6))[3],255)
        composited=Image.alpha_composite(Image.new("RGBA",ref.size,bg+(255,)),output)
        extrema=ImageChops.difference(composited,ref).convert("RGB").getextrema()
        self.assertLessEqual(max(v[1] for v in extrema),1)

    def test_near_white_screenshot_overshoot_does_not_leave_white_fringe(self):
        ref=Image.new('RGBA',(20,20),'#fdfaf2')
        ImageDraw.Draw(ref).rectangle((6,5,13,14),fill='#d28a42')
        ref.putpixel((5,4),(255,255,254,255))
        out=extract_matte(ref,'#fdfaf2')
        self.assertEqual(out.getpixel((5,4))[3],0)
        self.assertEqual(out.getpixel((9,9)),(210,138,66,255))

    def test_png_fallback_keeps_enclosed_white_emblem_opaque(self):
        ref=Image.new('RGBA',(24,24),'#fefbf8')
        ImageDraw.Draw(ref).rectangle((4,4,19,19),fill='#eab52d')
        ImageDraw.Draw(ref).rectangle((10,9,13,12),fill='white')
        result,_,_=transform(ref,{'matte_background':'#fefbf8','matte_preserve_regions':[[8,7,16,15]]})
        self.assertEqual(result.getpixel((11,10)),(255,255,255,255))
        self.assertEqual(result.getpixel((0,0))[3],0)

    def files(self,ref,render,background=None):
        ref.save(self.root/"ref.png");render.save(self.root/"actual.png")
        if background:background.save(self.root/"background.png")
        return self.root/"ref.png",self.root/"actual.png"

    def test_render_comparison_rejects_shifted_art(self):
        ref=Image.new("RGB",(60,40),"white");ImageDraw.Draw(ref).rectangle((10,10,30,30),fill="green")
        actual=ImageChops.offset(ref,2,0)
        reference,render=self.files(ref,actual)
        report=compare(reference,render,{},self.root/"report")
        self.assertFalse(report["passed"])
        self.assertTrue((self.root/"report"/"comparison.png").is_file())

    def test_font_allowance_does_not_ignore_changed_background(self):
        ref=Image.new("RGB",(100,100),"white");ImageDraw.Draw(ref).rectangle((20,20,25,30),fill="black")
        actual=ref.copy();ImageDraw.Draw(actual).rectangle((40,15,70,40),fill="#eeddaa")
        backdrop=Image.new("RGB",ref.size,"white");ImageDraw.Draw(backdrop).rectangle((40,15,70,40),fill="#eeddaa")
        reference,render=self.files(ref,actual,backdrop)
        config={"font_windows":[{"box":[10,10,80,45]}]}
        report=compare(reference,render,config,self.root/"report",self.root/"background.png")
        self.assertFalse(report["passed"])
        allowance=Image.open(self.root/"report"/"font-pixel-allowance.png")
        self.assertEqual(allowance.getpixel((50,25)),0)
        self.assertGreater(report["checked_fraction"],.95)

    def test_changed_glyphs_allowed_but_canvas_resize_not_allowed(self):
        ref=Image.new("RGB",(100,100),"white");ImageDraw.Draw(ref).rectangle((20,20,25,30),fill="black")
        actual=Image.new("RGB",ref.size,"white");ImageDraw.Draw(actual).rectangle((23,20,28,30),fill="black")
        reference,render=self.files(ref,actual,Image.new("RGB",ref.size,"white"))
        config={"font_windows":[{"box":[10,10,40,40]}]}
        report=compare(reference,render,config,self.root/"report",self.root/"background.png")
        self.assertTrue(report["passed"])
        actual.resize((101,100)).save(render)
        with self.assertRaisesRegex(ValueError,"dimensions differ"):
            compare(reference,render,config,self.root/"report")

    def test_excessive_exclusions_are_rejected(self):
        ref=Image.new("RGB",(100,100),"white")
        reference,render=self.files(ref,ref)
        with self.assertRaisesRegex(ValueError,"too much"):
            compare(reference,render,{"ignore_regions":[[0,0,90,100]]},self.root/"report")


if __name__=="__main__":unittest.main()
