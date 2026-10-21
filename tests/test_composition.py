from pathlib import Path
import copy
import sys
import unittest
from PIL import Image, ImageChops, ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from composition import validate_composition
from imaging import transform


class CompositionTests(unittest.TestCase):
    def background(self):
        return {"format":"png","composition":{"role":"background","code_reproducible":False,
          "decision":"Single-ended scenery fades into a plain, layout-independent base.",
          "embedded_ui":[],"layout_independent":True,"background":{
          "pattern":"top-only","strategy":"full-base","scale":"uniform","anchor":"top","base_color":"#f9f6f0"}}}

    def test_single_ended_full_base_allowed(self):
        validate_composition(self.background())

    def test_code_button_raster_rejected(self):
        a=self.background();a["composition"]["code_reproducible"]=True
        with self.assertRaisesRegex(ValueError,"code-reproducible"):validate_composition(a)

    def test_separate_simple_foreground_svg_allowed(self):
        validate_composition({"format":"svg","composition":{"role":"icon","code_reproducible":True,
          "decision":"Share the simple arrow; code owns its circular holder.","embedded_ui":[]}})

    def test_baked_arrow_or_icon_rejected(self):
        for item in ("arrow","poem-icon","module-shadow"):
            a=self.background();a["composition"]["embedded_ui"]=[item]
            with self.assertRaisesRegex(ValueError,"detach"):validate_composition(a)

    def test_background_fixed_to_modules_rejected(self):
        a=self.background();a["composition"]["layout_independent"]=False
        with self.assertRaisesRegex(ValueError,"independent"):validate_composition(a)

    def test_single_ended_hard_head_slice_rejected(self):
        a=self.background();a["composition"]["background"]["strategy"]="split-ends"
        with self.assertRaisesRegex(ValueError,"full base"):validate_composition(a)

    def test_scenery_stretch_or_tile_rejected(self):
        for scale in ("stretch","tile"):
            a=self.background();a["composition"]["background"]["scale"]=scale
            with self.assertRaises(ValueError):validate_composition(a)

    def test_bottom_only_requires_bottom_anchor(self):
        a=self.background();bg=a["composition"]["background"];bg["pattern"]="bottom-only"
        with self.assertRaisesRegex(ValueError,"bottom anchor"):validate_composition(a)
        bg["anchor"]="bottom";validate_composition(a)

    def test_both_ends_require_actual_height_decision(self):
        for strategy in ("split-ends","whole-proportional"):
            a=self.background();bg=a["composition"]["background"];bg.update(pattern="both",strategy=strategy)
            with self.assertRaisesRegex(ValueError,"height analysis"):validate_composition(a)
            bg["height_analysis"]="Short screen retains both motifs; extended screen preserves aspect and plain middle."
            validate_composition(a)

    def test_join_keeps_art_and_removes_all_lower_module_traces(self):
        im=Image.new("RGBA",(20,50),"#efeadf")
        draw=ImageDraw.Draw(im);draw.ellipse((1,1,12,7),fill="green");draw.rectangle((2,25,17,32),fill="white")
        result,_,_=transform(im,{"background_join":{"start_y":10,"transition":12,"base_color":"#f9f6f0","pattern_free":True}})
        self.assertIsNone(ImageChops.difference(result.crop((0,0,20,10)),im.crop((0,0,20,10))).getbbox(alpha_only=False))
        self.assertEqual(result.getpixel((5,10)),im.getpixel((5,9)))
        self.assertEqual(result.crop((0,21,20,50)).getextrema(),((249,249),(246,246),(240,240),(255,255)))

    def test_blend_preserves_alpha_and_leaves_art_unchanged(self):
        im=Image.new("RGBA",(20,40),(230,220,210,160));ImageDraw.Draw(im).ellipse((3,15,18,35),fill="green")
        for edge in ("top","bottom"):
            out,_,_=transform(im,{"edge_blend":[{"edge":edge,"pixels":8,"color":"#f9f6f0","pattern_free":True}]})
            self.assertEqual(out.getpixel((0,0 if edge=="top" else 39)),(249,246,240,160))
            box=(0,8,20,40) if edge=="top" else (0,0,20,32)
            self.assertIsNone(ImageChops.difference(out.crop(box),im.crop(box)).getbbox(alpha_only=False))

    def test_blending_without_confirmed_plain_band_rejected(self):
        im=Image.new("RGBA",(20,40),"white")
        for settings in ({"edge_blend":[{"edge":"top","pixels":8,"color":"#ffffff"}]},
                         {"background_join":{"start_y":10,"transition":10,"base_color":"#ffffff"}},
                         {"background_join":{"start_y":True,"transition":10,"base_color":"#ffffff","pattern_free":True}},
                         {"edge_blend":[{"edge":"top","pixels":41,"color":"#ffffff","pattern_free":True}]}):
            with self.assertRaises(ValueError):transform(im,copy.deepcopy(settings))


if __name__=="__main__":unittest.main()
