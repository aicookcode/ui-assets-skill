"""Executable extraction contract for decorative raster surfaces.

The agent identifies frame fragments and clean reference edges; this module
checks that its proposed mask can actually implement those decisions.
"""
from PIL import Image, ImageChops, ImageDraw
from imaging import box_valid, repair_mask


def background_ui_mask(asset, size, reference=None):
    """Declared source UI footprints must be fully removable, including shadows."""
    mask = Image.new('L', size, 0)
    if asset.get('composition', {}).get('role') != 'background':
        return mask
    audit = asset.get('composition', {}).get('surface', {}).get('source_ui')
    if (not isinstance(audit, dict) or not isinstance(audit.get('observation'), str)
            or not audit['observation'].strip() or not isinstance(audit.get('regions'), list)):
        raise ValueError('background requires source_ui observation and regions; inspect internal module surfaces, not just page corners')
    editable = repair_mask(size, asset.get('repair_regions', []), reference)
    join = asset.get('background_join')
    if join:
        from composition import validate_blending
        validate_blending(asset, size)
        start = join['start_y'] + join['transition']
        if start < size[1]:
            ImageDraw.Draw(editable).rectangle((0, start, size[0]-1, size[1]-1), fill=255)
    draw = ImageDraw.Draw(mask)
    for region in audit['regions']:
        if (not isinstance(region, dict) or region.get('kind') not in {'module', 'control'}
                or not box_valid(region.get('box'), size)):
            raise ValueError('source_ui region requires kind module/control and an exact in-canvas box including its outer shadow')
        box = region['box']
        if editable.crop(box).getextrema()[0] != 255:
            raise ValueError('background repair mask leaves source UI protected; cover the full footprint including corner exteriors, gaps and shadow')
        draw.rectangle((box[0], box[1], box[2]-1, box[3]-1), fill=255)
    return mask


def measure_background_ui(image, reference, asset):
    if asset.get('composition', {}).get('role') != 'background':
        return [], []
    background_ui_mask(asset, reference.size, reference)
    issues, measurements = [], []
    for region in asset['composition']['surface']['source_ui']['regions']:
        box = region['box']
        a, b = image.crop(box).convert('RGB'), reference.crop(box).convert('RGB')
        # Exact retention of an observed footprint is a definite failure. A changed
        # footprint is only evidence of editing, never proof of semantic cleanliness.
        unchanged = ImageChops.difference(a, b).getbbox() is None
        measurements.append({'box':box, 'source_ui_unchanged':unchanged})
        if unchanged:
            issues.append('observed source UI footprint is unchanged in background')
    return issues, measurements


def applies(asset):
    return (asset.get("format") != "svg" and
            asset.get("composition", {}).get("role") in {"background", "illustration", "texture"})


def validate_surface(asset, reference=None):
    if not applies(asset):
        return
    contract = asset.get("composition", {}).get("surface")
    if not isinstance(contract, dict):
        raise ValueError("decorative raster requires composition.surface; audit source corners and edge anchors")
    if contract.get("output") != "full-bleed":
        raise ValueError("decorative surface must be full-bleed; code owns container rounding")
    if contract.get("source_frame") not in {"none", "rounded-container"}:
        raise ValueError("surface requires an explicit source_frame observation")
    if not isinstance(contract.get("observation"), str) or not contract["observation"].strip():
        raise ValueError("surface requires a concrete source corner/edge observation")
    size = (asset["box"][2]-asset["box"][0], asset["box"][3]-asset["box"][1])
    ui_removed = background_ui_mask(asset, size, reference)
    if asset.get("trim") or asset.get("size"):
        raise ValueError("surface contract requires unchanged reference canvas; derive resized assets separately")
    corners = contract.get("corner_regions", {})
    expected = {"top_left": (0, 0), "top_right": (size[0]-1, 0),
                "bottom_left": (0, size[1]-1), "bottom_right": (size[0]-1, size[1]-1)}
    if not isinstance(corners, dict):
        raise ValueError("surface corner_regions must be an object")
    if contract["source_frame"] == "none" and corners:
        raise ValueError("unframed source cannot declare rounded corner repair regions")
    removed = Image.new("L", size, 0)
    draw = ImageDraw.Draw(removed)
    if contract["source_frame"] == "rounded-container":
        if contract.get('corner_content') not in {'plain','patterned'}:
            raise ValueError('corner_content must distinguish plain ground from patterned artwork')
        if contract.get('corner_content')=='patterned' and contract.get('corner_fill')!='generated-blend':
            raise ValueError('patterned corners require generated-blend; repeating edge pixels cannot reconstruct artwork')
        if set(corners) != set(expected) or asset.get("method") != "repair":
            raise ValueError("rounded source requires repair and all four explicit corner_regions")
        if all(value is None for value in corners.values()):
            raise ValueError("rounded source requires at least one clipped corner")
        editable = repair_mask(size, asset.get("repair_regions", []), reference)
        for name, regions in corners.items():
            if regions is None:  # This crop does not include that container corner.
                continue
            boxes = [regions] if box_valid(regions, size) else regions
            if not isinstance(boxes, list) or not boxes or any(not box_valid(box, size) for box in boxes):
                raise ValueError("surface corner outside reference")
            x, y = expected[name]
            if not any(box[0] <= x < box[2] and box[1] <= y < box[3] for box in boxes):
                raise ValueError("surface corner region does not touch its named corner")
            for box in boxes:
                # Ink-only/rounded masks do not necessarily cover the corner pixels.
                if editable.crop(box).getextrema()[0] != 255:
                    raise ValueError("repair mask does not cover declared source corner; text-only mask cannot remove rounding")
                draw.rectangle((box[0], box[1], box[2]-1, box[3]-1), fill=255)
    removed = ImageChops.lighter(removed, ui_removed)
    anchors = contract.get("edge_anchors")
    if not isinstance(anchors, list) or not anchors:
        raise ValueError("surface requires source edge_anchors to prevent added margins")
    anchor_mask = Image.new("L", size, 0)
    anchor_draw = ImageDraw.Draw(anchor_mask)
    for box in anchors:
        if not box_valid(box, size):
            raise ValueError("surface edge anchor outside reference")
        if not (box[0] == 0 or box[1] == 0 or box[2] == size[0] or box[3] == size[1]):
            raise ValueError("surface edge anchor must touch a source edge")
        anchor_draw.rectangle((box[0], box[1], box[2]-1, box[3]-1), fill=255)
    if ImageChops.multiply(anchor_mask, removed).getbbox():
        raise ValueError("source edge anchors cannot overlap corners or source UI being reconstructed")
    covered = ImageChops.lighter(anchor_mask, removed)
    if covered.crop((0, size[1]-1, size[0], size[1])).getextrema()[0] != 255:
        raise ValueError("surface bottom edge must be covered by clean source anchors or explicit corner/UI repairs")
    if asset.get("repair_regions"):
        editable = repair_mask(size, asset["repair_regions"], reference)
        if ImageChops.multiply(anchor_mask, editable).getbbox():
            raise ValueError("repair mask overlaps source edge anchors; it could introduce padding or shift artwork")


def measure_surface(image, reference, asset):
    if not applies(asset):
        return [], []
    validate_surface(asset, reference)
    issues, measurements = [], []
    for box in asset["composition"]["surface"]["edge_anchors"]:
        equal = ImageChops.difference(image.crop(box), reference.crop(box)).getbbox(alpha_only=False) is None
        measurements.append({"box": box, "pixel_equal": equal})
        if not equal:
            issues.append("surface source edge changed: added margin or displaced artwork")
    return issues, measurements


def corner_mask(asset, size):
    mask=Image.new('L',size,0)
    draw=ImageDraw.Draw(mask)
    for regions in asset.get('composition',{}).get('surface',{}).get('corner_regions',{}).values():
        if regions is None:continue
        boxes=[regions] if box_valid(regions,size) else regions
        for x0,y0,x1,y1 in boxes:draw.rectangle((x0,y0,x1-1,y1-1),fill=255)
    return mask


def complete_corners(image, reference, asset, generated=None):
    """Blend generated patterns, or smoothly interpolate a declared plain ground.

    Boundary colors constrain a harmonic field; no scanline copying, nearest
    neighbour enlargement, or repeated edge pixels are used to invent texture.
    """
    mask=corner_mask(asset,reference.size)
    if not mask.getbbox():return image,mask
    validate_surface(asset,reference)
    import numpy as np
    from PIL import Image
    contract=asset['composition']['surface']
    patterned=contract.get('corner_content')=='patterned'
    if patterned and generated is None:
        raise ValueError('patterned corner completion needs the normalized generated artwork, before source compositing')
    if generated is not None and generated.size!=reference.size:
        raise ValueError('generated corner canvas must match the reference')
    output=image.copy()
    for name,regions in contract.get('corner_regions',{}).items():
        if regions is None:continue
        local_asset={'composition':{'surface':{'corner_regions':{name:regions}}}}
        local_mask=corner_mask(local_asset,reference.size)
        x0,y0,x1,y1=local_mask.getbbox()
        box=(max(0,x0-2),max(0,y0-2),min(reference.width,x1+2),min(reference.height,y1+2))
        active=np.array(local_mask.crop(box))>0
        original=np.array(reference.crop(box).convert('RGBA'),dtype=np.float32)
        native=np.array((generated or image).crop(box).convert('RGBA'),dtype=np.float32)
        field=original[:,:,:3]-native[:,:,:3] if patterned else original[:,:,:3].copy()
        field[active]=0
        # Neumann continuation at the canvas edge, Dirichlet colors on visible
        # source pixels. This also works when a wedge touches two canvas edges.
        for iteration in range(10000):
            padded=np.pad(field,((1,1),(1,1),(0,0)),mode='edge')
            mean=(padded[:-2,1:-1]+padded[2:,1:-1]+padded[1:-1,:-2]+padded[1:-1,2:])*.25
            residual=float(np.max(np.abs(mean[active]-field[active])))
            field[active]=mean[active]
            if residual<.0002:break
        if residual>=.001:raise ValueError('corner color blending did not converge; inspect instead of repeating edge pixels')
        fixed=native.copy()
        rgb=native[:,:,:3]+field if patterned else field
        fixed[:,:,:3][active]=np.clip(rgb[active],0,255)
        fixed[:,:,3][active]=255
        tile=Image.fromarray(np.rint(fixed).astype('uint8')).convert(image.mode)
        output.paste(tile,box[:2],local_mask.crop(box))
    return output,mask
