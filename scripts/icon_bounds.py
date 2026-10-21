"""Tight SVG viewBox from a real, hash-bound transparent render; PNG uses alpha bbox."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from PIL import Image
from imaging import svg_check


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def trim_svg(source,render,vector_sha256,target):
    if Path(source).resolve()==Path(target).resolve():raise ValueError('preserve original SVG; use a new output path')
    if digest(source)!=vector_sha256:raise ValueError('SVG render binding hash changed')
    svg_check(source)
    im=Image.open(render).convert('RGBA')
    bounds=im.getchannel('A').getbbox()
    if bounds is None:raise ValueError('SVG render is empty')
    root=ET.parse(source).getroot()
    view=list(map(float,root.get('viewBox').replace(',',' ').split()))
    if abs(im.width*view[3]-im.height*view[2])>max(view[2:]):
        raise ValueError('SVG render aspect differs from source viewBox')
    x0=view[0]+bounds[0]/im.width*view[2]; y0=view[1]+bounds[1]/im.height*view[3]
    width=(bounds[2]-bounds[0])/im.width*view[2];height=(bounds[3]-bounds[1])/im.height*view[3]
    if min(width,height)<=0:raise ValueError('empty SVG content bounds')
    root.set('viewBox',' '.join(f'{n:.8f}' for n in [x0,y0,width,height]))
    root.set('width',f'{width:.8f}');root.set('height',f'{height:.8f}')
    ET.register_namespace('','http://www.w3.org/2000/svg')
    target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    ET.ElementTree(root).write(target,encoding='utf-8',xml_declaration=True)
    proof={'source_sha256':vector_sha256,'render_sha256':digest(render),'original_viewbox':view,
           'content_viewbox':[x0,y0,width,height],'alpha_bounds':list(bounds),'render_size':list(im.size),
           'output_sha256':digest(target)}
    target.with_suffix('.bounds.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
    return proof


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--svg',type=Path,required=True);ap.add_argument('--render',type=Path,required=True)
    ap.add_argument('--vector-sha256',required=True);ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();print(json.dumps(trim_svg(args.svg,args.render,args.vector_sha256,args.output)))
