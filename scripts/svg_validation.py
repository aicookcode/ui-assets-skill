"""Hash-bound source/render evidence, separate from the visual judgment itself."""
from pathlib import Path
import hashlib
import json
import math
from PIL import Image, ImageDraw, ImageFont
from imaging import load, svg_check

FEATURES=('outline','proportions','strokes','internal_marks','color','edges','layers')

def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def build(root,asset,version,source):
    root=Path(root)
    render=version['quality']['render_preview']
    target=root/render['file']
    crop=load(root/source['file']).crop(asset['box'])
    vector=root/version['file']
    size=svg_check(vector)
    image=load(target)
    offset=version.get('trim_offset',[0,0])
    width=max(crop.width,round(offset[0]+size[0]))+12
    height=max(crop.height,round(offset[1]+size[1]))+12
    reference=target.with_name(target.stem+'-source.png');crop.save(reference)
    frames=[]
    reference_frame=Image.new('RGBA',(width,height),'#faf8f3')
    reference_frame.alpha_composite(crop,(6,6));frames.append(reference_frame)
    sx,sy=image.width/size[0],image.height/size[1]
    # Integrate high-resolution coverage before returning to native pixels.
    # Direct large-step affine sampling would turn smooth vectors into jagged shapes.
    factor=max(8,min(32,math.ceil(max(sx,sy))))
    aligned=image.transform((width*factor,height*factor),Image.Transform.AFFINE,
        (sx/factor,0,-(6+offset[0])*sx,0,sy/factor,-(6+offset[1])*sy),Image.Resampling.BICUBIC)
    aligned=aligned.resize((width,height),Image.Resampling.LANCZOS)
    frame=Image.new('RGBA',(width,height),'#faf8f3');frame.alpha_composite(aligned);frames.append(frame)
    scale=6
    board=Image.new('RGB',(max(500,width*scale*2+40),height*scale+height+100),'#eae7df')
    draw=ImageDraw.Draw(board)
    fontfile=Path('C:/Windows/Fonts/msyh.ttc')
    font=ImageFont.truetype(str(fontfile),14) if fontfile.exists() else ImageFont.load_default()
    half=board.width//2
    for i,(frame,label) in enumerate(zip(frames,('Source / 原设计','Actual SVG / 真实渲染'))):
        draw.text((i*half+12,8),label,font=font,fill='#222222')
        board.paste(frame,(i*half+(half-width)//2,32),frame)
        enlarged=frame.resize((width*scale,height*scale),Image.Resampling.NEAREST)
        board.paste(enlarged,(i*half+(half-enlarged.width)//2,height+60),enlarged)
    comparison=target.with_name(target.stem+'-source-comparison.png');board.save(comparison)
    evidence={'version':version['number'],'source':source['id'],'source_sha256':source['sha256'],
        'reference_box':asset['box'],'source_crop':str(reference.relative_to(root)),
        'source_crop_sha256':digest(reference),'vector_sha256':digest(vector),
        'render_sha256':digest(target),'file':str(comparison.relative_to(root)),
        'sha256':digest(comparison),'scale':scale,'trim_offset':offset,
        'alignment':'native SVG dimensions at recorded source-space trim_offset; no aspect distortion'}
    metadata=comparison.with_suffix('.json');metadata.write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    return evidence

def validate(root,asset,version,source,checks):
    root=Path(root);q=version['quality'];e=q.get('source_comparison')
    if not e:
        raise ValueError('SVG requires source comparison evidence; register its actual render first')
    def bound_file(relative,expected):
        path=(root/relative).resolve()
        return path.is_relative_to(root.resolve()) and path.is_file() and digest(path)==expected
    render=q.get('render_preview',{})
    if (e.get('version')!=version['number'] or e.get('source')!=source['id']
        or e.get('trim_offset')!=version.get('trim_offset',[0,0])
        or e.get('reference_box')!=asset['box'] or e.get('source_sha256')!=source['sha256']
        or not bound_file(source['file'],e.get('source_sha256'))
        or not bound_file(version['file'],e.get('vector_sha256'))
        or not bound_file(render.get('file',''),e.get('render_sha256'))
        or not bound_file(e.get('source_crop',''),e.get('source_crop_sha256'))
        or not bound_file(e.get('file',''),e.get('sha256'))):
        raise ValueError('SVG source comparison evidence is stale or changed')
    acknowledgment=checks.get('icon_fidelity',{}).get('comparison_sha256')
    if acknowledgment!=e['sha256']:
        raise ValueError('SVG review must acknowledge the exact source comparison_sha256 viewed')

def feature_review(entry):
    features=entry.get('features',{})
    if not isinstance(features,dict):
        raise ValueError('icon_fidelity features must be a per-feature object')
    for key in FEATURES:
        feature=features.get(key,{})
        if (not isinstance(feature,dict) or feature.get('verdict') not in {'passed','issues','unknown'}
            or any(not isinstance(feature.get(k),str) or not feature[k].strip() for k in ('source','output'))
            or not isinstance(feature.get('differences'),list)
            or any(not isinstance(x,str) or not x.strip() for x in feature.get('differences',[]))):
            raise ValueError('icon_fidelity requires separate source/output feature observation: '+key)
        if feature['verdict']=='passed' and feature['differences']:
            raise ValueError('passed feature cannot retain differences: '+key)
    recheck=entry.get('independent_review',{})
    if (not isinstance(recheck,dict) or recheck.get('verdict') not in {'passed','issues','unknown'}
        or not str(recheck.get('note','')).strip() or not isinstance(recheck.get('differences'),list)):
        raise ValueError('icon_fidelity requires an independent second visual review')
    if entry.get('verdict')=='passed' and (any(features[k]['verdict']!='passed' for k in FEATURES)
            or recheck['verdict']!='passed' or recheck['differences']):
        raise ValueError('icon pass cannot override feature or independent review differences')
