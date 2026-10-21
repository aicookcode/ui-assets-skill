"""Detect source silhouettes cut by a proposed crop, before transparent trimming.

The automatic test applies to raster foregrounds on a declared near-flat matte.
Other source types still receive a context image for semantic edge inspection.
"""
from pathlib import Path
import hashlib,json
from PIL import ImageDraw


def inspect(source, asset, destination=None):
    if asset.get('composition',{}).get('role')!='icon':return None
    l,t,r,b=asset['box'];margin=6;w,h=source.size
    context_box=[max(0,l-margin),max(0,t-margin),min(w,r+margin),min(h,b+margin)]
    context=source.crop(context_box).convert('RGB')
    report={'source_box':[l,t,r,b],'context_box':context_box,
            'context_rgb_sha256':hashlib.sha256(context.tobytes()).hexdigest(),
            'automatic_method':'unavailable','crossing_edges':[],
            'note':'Context inspection remains required; no detected crossing is not proof of completeness.'}
    matte=asset.get('matte_background')
    if matte and asset.get('format')!='svg':
        bg=tuple(int(matte[i:i+2],16) for i in (1,3,5));pixels=source.convert('RGB').load()
        def foreground(p):return max(abs(p[c]-bg[c]) for c in range(3))>=24
        def connected(a,b):return foreground(a) and foreground(b) and max(abs(a[c]-b[c]) for c in range(3))<=48
        edges={
            'left':[((l,y),(l-1,y)) for y in range(t,b)] if l else [],
            'right':[((r-1,y),(r,y)) for y in range(t,b)] if r<w else [],
            'top':[((x,t),(x,t-1)) for x in range(l,r)] if t else [],
            'bottom':[((x,b-1),(x,b)) for x in range(l,r)] if b<h else []}
        runs={}
        for name,pairs in edges.items():
            longest=run=0
            for inside,outside in pairs:
                run=run+1 if connected(pixels[inside],pixels[outside]) else 0
                longest=max(longest,run)
            runs[name]=longest
            if longest>=2:report['crossing_edges'].append(name)
        report.update(automatic_method='flat-matte-adjacent-continuation-v1',
            foreground_delta=24,color_continuity_delta=48,min_adjacent_run=2,edge_runs=runs)
    if destination:
        destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
        token=hashlib.sha256(json.dumps([asset['id'],asset['box'],report['context_rgb_sha256']]).encode()).hexdigest()[:12]
        file=destination/(asset['id']+'-'+token+'.png')
        marked=context.copy();draw=ImageDraw.Draw(marked)
        draw.rectangle((l-context_box[0],t-context_box[1],r-context_box[0]-1,b-context_box[1]-1),outline='#ee3344',width=1)
        marked.resize((marked.width*4,marked.height*4)).save(file)
        report['context_file']=str(file.resolve())
        file.with_suffix('.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def require_complete(report):
    if report and report['crossing_edges']:
        raise ValueError('source icon continues beyond crop on '+', '.join(report['crossing_edges'])+
            '; inspect context and correct the source box before trimming; output alpha bounds cannot prove source completeness')
