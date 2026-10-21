"""Match repaired ink pixels to the surrounding ORIGINAL paper, without editing it."""
import numpy as np
from PIL import Image


def match_ink_boundaries(candidate, reference, regions, mask_for):
    output=candidate.copy()
    evidence=[]
    for index,region in enumerate(regions):
        if not isinstance(region,dict) or not ('ink_threshold' in region or region.get('blend')=='boundary'):
            continue
        mask=mask_for(reference.size,[region],reference)
        bounds=mask.getbbox()
        if not bounds:continue
        x0,y0,x1,y1=bounds
        box=(max(0,x0-2),max(0,y0-2),min(reference.width,x1+2),min(reference.height,y1+2))
        native=np.array(candidate.crop(box),dtype=np.float32)
        original=np.array(reference.crop(box),dtype=np.float32)
        editable=np.array(mask.crop(box))>0
        if editable[0].any() or editable[-1].any() or editable[:,0].any() or editable[:,-1].any():
            raise ValueError('ink repair needs visible original paper around its mask; revise its text window')
        # Interpolate only the color DIFFERENCE from the unedited boundary.
        # Copying the source RGB here would copy the old lettering back in.
        delta=original[:,:,:3]-native[:,:,:3]
        delta[editable]=0
        active=editable[1:-1,1:-1]
        residual=0.
        for iteration in range(10000):
            average=(delta[:-2,1:-1]+delta[2:,1:-1]+delta[1:-1,:-2]+delta[1:-1,2:])*.25
            center=delta[1:-1,1:-1]
            residual=float(np.max(np.abs(average[active]-center[active]))) if active.any() else 0.
            center[active]=average[active]
            if residual<.0002:break
        if residual>=.001:raise ValueError('ink boundary correction did not converge; do not silently use hard paste')
        fixed=native.copy()
        fixed[:,:,:3][editable]=np.clip(native[:,:,:3][editable]+delta[editable],0,255)
        tile=Image.fromarray(np.rint(fixed).astype('uint8'))
        output.paste(tile,box[:2],mask.crop(box))
        evidence.append({'region':index,'method':'harmonic-boundary-color-difference',
                         'iterations':iteration+1,'residual':round(residual,5),
                         'pixels':int(editable.sum()),'reference_pixels_outside_mask':'unchanged'})
    return output,evidence
