"""Deterministic pixel operations; no semantic segmentation or image generation."""
from collections import deque
from pathlib import Path
import re
import math
import xml.etree.ElementTree as ET

from PIL import Image, ImageChops, ImageDraw, ImageOps, ImageStat, ImageFilter


def repair_mask(size, regions, reference=None):
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for region in regions:
        box = region.get("box") if isinstance(region, dict) else region
        if not box_valid(box, size):
            raise ValueError("repair region outside asset")
        left, top, right, bottom = box
        if isinstance(region, dict):
            if set(region) - {"box", "ink_threshold", "dilate", "ink_mode", "radius","blend"}:
                raise ValueError("unsupported repair region fields")
            if 'blend' in region and region['blend']!='boundary':
                raise ValueError('repair blend must be boundary')
            if "radius" in region:
                radius=region["radius"]
                if type(radius) is not int or not 0 <= radius <= min(right-left,bottom-top)/2:
                    raise ValueError("repair radius outside region dimensions")
                if any(key in region for key in ("ink_threshold","ink_mode","dilate")):
                    raise ValueError("rounded repair region cannot also be an ink mask")
                draw.rounded_rectangle((left,top,right-1,bottom-1),radius=radius,fill=255)
                continue
            if region.get('blend')=='boundary' and 'ink_threshold' not in region:
                draw.rectangle((left,top,right-1,bottom-1),fill=255)
                continue
            threshold, dilate = region.get("ink_threshold", 180), region.get("dilate", 1)
            if type(threshold) is not int or not 0 <= threshold <= 255 or type(dilate) is not int or not 0 <= dilate <= 3:
                raise ValueError("ink_threshold must be 0..255 and dilate 0..3")
            if region.get("ink_mode", "dark") not in {"dark", "light"}:
                raise ValueError("ink_mode must be dark or light")
            if reference is not None:
                light = region.get("ink_mode") == "light"
                ink = reference.crop(box).convert("L").point(lambda value: 255 if (value >= threshold if light else value <= threshold) else 0)
                if dilate:
                    ink = ink.filter(ImageFilter.MaxFilter(dilate*2+1))
                # Regions form a union. An overlapping glyph window must not erase
                # an earlier full repair band with its zero-valued background.
                mask.paste(ImageChops.lighter(mask.crop(box), ink), (left, top))
                continue
        draw.rectangle((left, top, right-1, bottom-1), fill=255)
    return mask


def preserve_reference(candidate, reference, regions, blend_evidence=None):
    """White regions may change; every pixel outside them is copied from the baseline."""
    if candidate.size != reference.size:
        raise ValueError("masked repair requires candidate at exact reference dimensions")
    mask = repair_mask(reference.size, regions, reference)
    from repair_blend import match_ink_boundaries
    candidate,corrections = match_ink_boundaries(candidate,reference,regions,repair_mask)
    if blend_evidence is not None:blend_evidence.extend(corrections)
    return Image.composite(candidate, reference, mask), mask


def extract_matte(im, background):
    """Unmix a small flat-background graphic, including enclosed holes and antialiasing.

    Preserve opaque interior RGB, and infer boundary alpha from neighbouring opaque
    colour. It is not semantic segmentation and is unsuitable for texture.
    """
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
        raise ValueError("matte_background must be #RRGGBB")
    bg = tuple(int(background[i:i+2], 16) for i in (1, 3, 5))
    result = Image.new("RGBA", im.size)
    pixels = list(im.get_flattened_data()) if hasattr(im,"get_flattened_data") else list(im.getdata())
    distances = [max(abs(p-b) for p,b in zip(pixel[:3],bg)) for pixel in pixels]
    out = []
    for index,pixel in enumerate(pixels):
        if min(bg)>=230 and all(p>=b-12 for p,b in zip(pixel[:3],bg)):
            out.append((0,0,0,0))
            continue
        # Screenshot quantization can overshoot a nearly white matte by 1--2
        # values. Treating that as genuine white paint forces alpha=1 and leaves
        # opaque white specks around an otherwise transparent colored icon.
        minimum_alpha = max((max(0, b-p-2)/max(1, b) if p <= b else max(0,p-b-2)/max(1, 255-b))
                    for p, b in zip(pixel[:3], bg))
        if distances[index] < 4:
            out.append((0, 0, 0, 0))
        else:
            x,y=index%im.width,index//im.width
            neighbours=[yy*im.width+xx for yy in range(max(0,y-2),min(im.height,y+3))
                         for xx in range(max(0,x-2),min(im.width,x+3))]
            near_background=any(distances[pos]<5 for pos in neighbours)
            alpha=1.0
            if near_background:
                candidate=max(neighbours,key=lambda pos:distances[pos])
                direction=[p-b for p,b in zip(pixels[candidate][:3],bg)]
                delta=[p-b for p,b in zip(pixel[:3],bg)]
                norm=sum(v*v for v in direction)
                estimate=sum(a*b for a,b in zip(delta,direction))/norm if norm else 1
                residual=max(abs(a-estimate*b) for a,b in zip(delta,direction))
                if residual<=6:
                    alpha=max(minimum_alpha,min(1,estimate))
            alpha_byte = min(255, math.ceil(alpha*255))
            a = alpha_byte/255
            foreground = tuple(max(0, min(255, round((p-b)/a+b))) for p, b in zip(pixel[:3], bg))
            out.append((*foreground, round(alpha_byte*pixel[3]/255)))
    result.putdata(out)
    return result


def load(path):
    with Image.open(path) as im:
        im.load()
        return ImageOps.exif_transpose(im).convert("RGBA")


def box_valid(box, size):
    return (isinstance(box, list) and len(box) == 4
            and all(type(v) is int for v in box)
            and 0 <= box[0] < box[2] <= size[0]
            and 0 <= box[1] < box[3] <= size[1])


def remove_edge_background(im, color, tolerance=0):
    """Only remove boundary-connected pixels matching an explicitly chosen color."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color) or not 0 <= tolerance <= 30:
        raise ValueError("background color must be #RRGGBB; tolerance must be 0..30")
    im = im.copy()
    rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    w, h = im.size
    px = im.load()
    seen = bytearray(w * h)
    queue = deque()
    def add(x, y):
        pos = y * w + x
        if not seen[pos]:
            seen[pos] = 1
            p = px[x, y]
            if max(abs(p[i] - rgb[i]) for i in range(3)) <= tolerance:
                queue.append((x, y))
    for x in range(w):
        add(x, 0)
        add(x, h - 1)
    for y in range(h):
        add(0, y)
        add(w - 1, y)
    while queue:
        x, y = queue.popleft()
        px[x, y] = (*px[x, y][:3], 0)
        for xx, yy in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
            if 0 <= xx < w and 0 <= yy < h:
                add(xx, yy)
    return im


def transform(im, asset):
    from composition import validate_blending
    validate_blending(asset, im.size)
    warnings = []
    offset = [0, 0]
    if asset.get("background_join"):
        settings=asset["background_join"]
        start,transition=settings["start_y"],settings["transition"]
        color=settings["base_color"]
        if not 0<start<im.height or not 1<=transition<=im.height-start or not re.fullmatch(r"#[0-9a-fA-F]{6}",color):
            raise ValueError("invalid background join")
        # The designated region is pattern-free: reconstruct only the colour continuation,
        # never resize or repeat the artwork. First continuation row matches the boundary.
        base=tuple(int(color[i:i+2],16) for i in (1,3,5))
        im=im.copy(); px=im.load()
        boundary=[px[x,start-1] for x in range(im.width)]
        for y in range(start,im.height):
            t=min(1,(y-start)/max(1,transition-1));t=t*t*(3-2*t)
            for x in range(im.width):
                px[x,y]=tuple(round(boundary[x][channel]*(1-t)+base[channel]*t) for channel in range(3))+(255,)
        warnings.append("Pattern-free background continuation reconstructed; artwork was not stretched.")
    for blend in asset.get("edge_blend",[]):
        edge,count,color=blend["edge"],blend["pixels"],blend["color"]
        if edge not in {"top","bottom"} or not 1<=count<=im.height or not re.fullmatch(r"#[0-9a-fA-F]{6}",color) or blend.get("pattern_free") is not True:
            raise ValueError("edge blending requires a declared pattern-free band and valid colour")
        base=tuple(int(color[i:i+2],16) for i in (1,3,5))
        im=im.copy(); px=im.load()
        for distance in range(count):
            y=distance if edge=="top" else im.height-1-distance
            t=distance/max(1,count-1);t=t*t*(3-2*t)
            for x in range(im.width):
                original=px[x,y]
                px[x,y]=tuple(round(base[c]*(1-t)+original[c]*t) for c in range(3))+(original[3],)
        warnings.append("Plain edge band reconciled to base colour; inspect the colour seam.")
    if asset.get("matte_background"):
        original=im.copy()
        im = extract_matte(im, asset["matte_background"])
        for box in asset.get('matte_preserve_regions',[]):
            if not box_valid(box,original.size):raise ValueError('matte foreground preserve box outside image')
            im.paste(original.crop(box),box[:2])
        warnings.append("Flat-background alpha unmixing; review on the specified backdrop and a dark backdrop.")
    if asset.get("remove_background"):
        bg = asset["remove_background"]
        im = remove_edge_background(im, bg["color"], bg.get("tolerance", 0))
        warnings.append("Background removed by boundary color; inspect edges and shadows.")
    if asset.get("trim") or asset.get('composition',{}).get('role')=='icon':
        bounds = im.getchannel("A").getbbox()
        if bounds is None:
            raise ValueError("asset is fully transparent")
        offset = list(bounds[:2])
        im = im.crop(bounds)
    original = im.size
    target = asset.get("size")
    if target and tuple(target) != original:
        # Allow rounding by at most one pixel, but never stretch arbitrarily.
        if abs(target[0] * original[1] - target[1] * original[0]) > max(original):
            raise ValueError("target size would distort aspect ratio")
        if target[0] > original[0] or target[1] > original[1]:
            warnings.append("Resampled upwards; no claim of recovered high-resolution detail.")
        im = im.resize(tuple(target), Image.Resampling.LANCZOS)
    return im, offset, warnings


def svg_check(path):
    raw = Path(path).read_text(encoding="utf-8")
    if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper():
        raise ValueError("SVG entities/doctype are not allowed")
    root = ET.fromstring(raw)
    if root.tag.split("}")[-1] != "svg":
        raise ValueError("not an SVG")
    allowed = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
               "polygon", "defs", "linearGradient", "radialGradient", "stop", "clipPath",
               "mask", "title", "desc"}
    for node in root.iter():
        if node.tag.split("}")[-1] not in allowed:
            raise ValueError("SVG contains unsupported active/external content")
        for key, val in node.attrib.items():
            key = key.split("}")[-1].lower()
            if key.startswith("on") or key in {"href", "style"}:
                raise ValueError("SVG event, external reference or style is not allowed")
            if "url(" in val.lower() and not re.fullmatch(r"url\(#[\w-]+\)", val):
                raise ValueError("external SVG resource is not allowed")
    view = root.get("viewBox", "").replace(",", " ").split()
    if len(view) != 4 or not all(math.isfinite(float(v)) for v in view) or float(view[2]) <= 0 or float(view[3]) <= 0:
        raise ValueError("SVG requires a positive viewBox")
    return [float(view[2]), float(view[3])]


def internal_alpha_holes(im):
    """Screen for enclosed transparent regions, not semantic proof of an error."""
    alpha = im.getchannel("A")
    alpha.thumbnail((384, 384))
    w, h = alpha.size
    pixels = list(alpha.get_flattened_data()) if hasattr(alpha, "get_flattened_data") else list(alpha.getdata())
    seen = bytearray(w*h)
    holes = 0
    for start in range(w*h):
        if seen[start] or pixels[start] > 16:
            continue
        seen[start] = 1
        queue = deque([start])
        area, edge = 0, False
        while queue:
            i = queue.popleft()
            x, y = i % w, i // w
            area += 1
            edge = edge or x == 0 or y == 0 or x == w-1 or y == h-1
            neighbours = []
            if x: neighbours.append(i-1)
            if x+1 < w: neighbours.append(i+1)
            if y: neighbours.append(i-w)
            if y+1 < h: neighbours.append(i+w)
            for j in neighbours:
                if not seen[j] and pixels[j] <= 16:
                    seen[j] = 1
                    queue.append(j)
        if not edge and area >= 4:
            holes += 1
    return holes


def qa(path, asset, reference=None, warnings=()):
    if Path(path).suffix == ".svg":
        size = svg_check(path)
        return {"status": "unknown", "size": size, "issues": [],
                "notes": ["SVG validated structurally; visual fidelity requires review."],
                "pixel_equal": None, "page_responsive_tested": False}
    im = load(path)
    alpha = im.getchannel("A")
    bounds = alpha.getbbox()
    issues, notes = [], list(warnings)
    if bounds is None:
        issues.append("empty image")
    if asset.get("transparent") and alpha.getextrema()[0] == 255:
        issues.append("transparency required but no transparent pixels exist")
    holes = internal_alpha_holes(im) if asset.get("transparent") and bounds else 0
    if holes:
        if asset.get("alpha_policy") == "solid":
            issues.append("unexpected internal transparent holes in solid silhouette")
        else:
            notes.append("Enclosed transparent regions detected; compare holes with the original design.")
    if (asset.get("trim") or asset.get('composition',{}).get('role')=='icon') and bounds and bounds != (0, 0, *im.size):
        issues.append("remaining outer transparent padding")
    if asset.get("size") and list(im.size) != asset["size"]:
        issues.append("incorrect output dimensions")
    if reference is not None and asset["method"] in {"repair","generate"} and not asset.get("trim"):
        if abs(im.width*reference.height-im.height*reference.width)>max(reference.size):
            issues.append("output aspect ratio differs from reference")
    if reference is not None and asset["method"] in {"repair", "generate"} and not asset.get("trim"):
        expected = tuple(asset.get("size") or reference.size)
        if im.size != expected:
            issues.append("native output dimensions differ from the approved canvas")
    pixel_equal = None
    if reference is not None and reference.size == im.size:
        pixel_equal = ImageChops.difference(im, reference).getbbox(alpha_only=False) is None
        if not pixel_equal:
            notes.append("Pixels differ from reference; inspect fidelity.")
    if asset.get("transparent") and bounds:
        extrema = [alpha.crop(b).getextrema()[1] for b in (
            (0, 0, im.width, 1), (0, im.height-1, im.width, im.height),
            (0, 0, 1, im.height), (im.width-1, 0, im.width, im.height))]
        if any(extrema):
            notes.append("Visible content touches an edge; inspect possible clipping (may be intentional after trim).")
    gray = im.convert("L")
    contrast = round(ImageStat.Stat(gray).stddev[0], 3)
    # Contrast is a metric, not a semantic blur detector.
    review = bool(notes) or asset["method"] not in {"crop", "reuse"} or pixel_equal is not True
    return {"status": "issues" if issues else "unknown" if review else "passed",
            "size": list(im.size), "issues": issues, "notes": notes,
            'content_bounds':list(bounds) if bounds else None,'alpha_range':list(alpha.getextrema()),
            "pixel_equal": pixel_equal, "contrast_stddev": contrast, "internal_alpha_holes": holes,
            "page_responsive_tested": False}


def nine_slice(im, margins, directory):
    left, top, right, bottom = margins
    if min(margins) <= 0 or left + right >= im.width or top + bottom >= im.height:
        raise ValueError("9-slice margins must leave positive corners, edges and center")
    directory.mkdir(parents=True, exist_ok=False)
    xs, ys = [0, left, im.width-right, im.width], [0, top, im.height-bottom, im.height]
    names = (("top-left", "top", "top-right"), ("left", "center", "right"),
             ("bottom-left", "bottom", "bottom-right"))
    result = []
    for row in range(3):
        for col in range(3):
            target = directory / (names[row][col] + ".png")
            box = [xs[col], ys[row], xs[col+1], ys[row+1]]
            im.crop(box).save(target)
            result.append({"file": target.name, "box": box})
    return result


def contact_sheet(items, target):
    columns, cell_w, cell_h = 4, 240, 210
    sheet = Image.new("RGB", (columns*cell_w, max(1, (len(items)+3)//4)*cell_h), "#eeeeee")
    draw = ImageDraw.Draw(sheet)
    for index, (label, path) in enumerate(items):
        x, y = (index % columns)*cell_w, (index//columns)*cell_h
        # Checkerboard makes alpha visible.
        for yy in range(y, y+170, 10):
            for xx in range(x, x+cell_w, 10):
                draw.rectangle((xx, yy, xx+9, yy+9), fill="#cccccc" if ((xx-x)//10+(yy-y)//10)%2 else "white")
        if path and Path(path).suffix != ".svg":
            thumb = load(path)
            thumb.thumbnail((220, 160))
            sheet.paste(thumb, (x+(cell_w-thumb.width)//2, y+(170-thumb.height)//2), thumb)
        else:
            draw.text((x+15, y+70), "SVG - inspect source separately", fill="black")
        draw.text((x+8, y+180), label.encode("ascii", "replace").decode()[:33], fill="black")
    sheet.save(target)
