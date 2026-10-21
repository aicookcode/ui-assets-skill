import sys
import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import icon_bounds
from imaging import transform


class IconBoundsTests(unittest.TestCase):
    def test_png_tight_crop_keeps_faint_shadow_and_offset(self):
        im=Image.new('RGBA',(30,20),(0,0,0,0))
        ImageDraw.Draw(im).rectangle((8,5,18,13),fill='green')
        im.putpixel((6,4),(12,20,12,1))
        out,offset,_=transform(im,{'composition':{'role':'icon'}})
        self.assertEqual(offset,[6,4]);self.assertEqual(out.size,(13,10))
        self.assertEqual(out.getchannel('A').getbbox(),(0,0,13,10))
        self.assertEqual(out.getpixel((0,0))[3],1)

    def test_svg_bounds_change_viewbox_without_changing_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);source=folder/'source.svg';render=folder/'render.png';target=folder/'tight.svg'
            source.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 30"><rect x="5" y="4" width="10" height="8" fill="red"/></svg>')
            im=Image.new('RGBA',(80,60),(0,0,0,0));ImageDraw.Draw(im).rectangle((10,8,29,23),fill='red');im.save(render)
            proof=icon_bounds.trim_svg(source,render,icon_bounds.digest(source),target)
            root=ET.parse(target).getroot()
            self.assertEqual(list(map(float,root.get('viewBox').split())),[5,4,10,8])
            self.assertEqual(root[0].get('x'),'5');self.assertEqual(root[0].get('width'),'10')
            self.assertEqual(proof['output_sha256'],icon_bounds.digest(target))
            with self.assertRaisesRegex(ValueError,'hash changed'):
                icon_bounds.trim_svg(source,render,'wrong',target)


if __name__=='__main__':unittest.main()
