"""Bind tool-sized edit canvases to exact source coordinates; never stretch artwork."""
from pathlib import Path
import hashlib
from PIL import Image, ImageDraw, ImageChops, ImageStat
from imaging import load


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(task, crop):
    """An integer enlargement inside a square carrier preserves source geometry."""
    ref = load(crop)
    factor = max(1, 960 // max(ref.size))
    side = max(1024, max(ref.size) + 64)
    size = (ref.width * factor, ref.height * factor)
    x, y = (side-size[0])//2, (side-size[1])//2
    carrier = Image.new('RGBA', (side,side), '#d5d0c8')
    carrier.alpha_composite(ref.resize(size, Image.Resampling.NEAREST), (x,y))
    path = Path(crop).with_name(Path(crop).stem+'-carrier.png')
    carrier.save(path)
    task['canvas_mapping'] = {'policy':'square-carrier-v1','reference_size':list(ref.size),
        'carrier_size':[side,side], 'content_box':[x,y,x+size[0],y+size[1]],
        'integer_scale':factor, 'file':str(path.resolve()), 'sha256':digest(path)}
    task['reference'] = str(path.resolve())
    task['prompt'] += (f'\nEdit the supplied square carrier in place. Keep the entire square canvas and '
        f'the gray padding unchanged. The only asset is inside {task["canvas_mapping"]["content_box"]} '
        f'on the {side}x{side} carrier. Do not move, enlarge, shrink or reframe that asset. '
        'Do not add a frame. The script will remove only the recorded carrier padding and map '
        'the repair pixels back to the exact source canvas. Output a square image.')


def normalize(image, reference, mapping=None):
    """Only a bound carrier or proportional legacy size can map onto the source."""
    if image.size == reference.size:
        return image, {'policy':'identity','native_size':list(image.size),'output_size':list(reference.size)}
    if mapping:
        if mapping.get('policy') != 'square-carrier-v1' or mapping['reference_size'] != list(reference.size):
            raise ValueError('canvas mapping does not match the exact reference')
        cw,ch = mapping['carrier_size']
        # At most one native pixel of integer rounding; no arbitrary aspect coercion.
        if abs(image.width*ch/cw-image.height) > 1:
            raise ValueError('canvas carrier aspect changed; exact reference mapping rejected')
        if digest(mapping['file']) != mapping['sha256']:
            raise ValueError('canvas carrier hash changed')
        probe=image.convert('RGB').resize((cw,ch),Image.Resampling.BILINEAR)
        outside=Image.new('L',(cw,ch),255)
        x0,y0,x1,y1=mapping['content_box']
        ImageDraw.Draw(outside).rectangle((max(0,x0-3),max(0,y0-3),min(cw,x1+3),min(ch,y1+3)),fill=0)
        delta=ImageChops.difference(probe,Image.new('RGB',probe.size,'#d5d0c8'))
        padding_error=sum(ImageStat.Stat(delta,outside).mean)/3
        if padding_error>8:
            raise ValueError('canvas carrier padding changed; content alignment must be regenerated')
        sx,sy=image.width/cw,image.height/ch
        box=[mapping['content_box'][i]*(sx if i%2==0 else sy) for i in range(4)]
        result=image.resize(reference.size,Image.Resampling.BICUBIC,box=tuple(box))
        return result, {**mapping,'native_size':list(image.size),'sample_box':box,'padding_mean_error':padding_error,
                        'output_size':list(reference.size),'requires_visual_alignment_review':True}
    # Legacy exact-crop requests have no carrier; only rounding-equivalent ratios qualify.
    scale=min(image.width/reference.width,image.height/reference.height)
    if abs(image.width-scale*reference.width)>1 or abs(image.height-scale*reference.height)>1:
        raise ValueError('canvas aspect changed; exact reference needs a bound carrier, never stretch/crop to fit')
    return image.resize(reference.size,Image.Resampling.LANCZOS), {
        'policy':'proportional-reference-v1','native_size':list(image.size),
        'output_size':list(reference.size),'requires_visual_alignment_review':True}
