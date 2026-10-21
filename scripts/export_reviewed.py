"""Export the explicitly accepted displayed snapshot; retain visual assessments."""
from pathlib import Path
from uuid import uuid4
from zipfile import ZipFile,ZIP_DEFLATED
import hashlib,json,shutil
from PIL import Image
from workflow import validate_confirmation

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def export(root,p,summary_path,evidence,current,verify):
    evidence=validate_confirmation(evidence,'export-reviewed')
    root=Path(root).resolve();summary_path=Path(summary_path).resolve()
    if not summary_path.is_relative_to(root/'previews'):raise ValueError('use the displayed project preview')
    summary=json.loads(summary_path.read_text(encoding='utf-8'))
    assets=[a for a in p['assets'] if a['selected']];items=summary.get('items',[])
    if (summary.get('kind')!='review_only_summary' or len(items)!=len(assets)
        or {x['id'] for x in items}!={a['id'] for a in assets}):
        raise ValueError('displayed snapshot selection differs from current selection')
    resolved=[]
    for a in assets:
        item=next(i for i in items if i['id']==a['id'])
        path=(root/item['file']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('displayed asset path escapes project')
        if not path.is_file() or item['sha256']!=digest(path):
            raise ValueError('displayed snapshot is stale or changed')
        # A displayed best candidate can be older than the latest attempted version.
        versions=list(a.get('versions',[]))
        latest=current(a)
        if latest:versions.append(latest)
        v=next((v for v in versions if (root/v['file']).resolve()==path),None)
        if v is None:
            # Readable retained candidates without an imported version are exportable too.
            size=list(Image.open(path).size) if a['format']!='svg' else None
            v={'file':item['file'],'number':None,'trim_offset':[0,0],
               'quality':{'size':size,'snapshot_findings':item.get('findings',[])}}
        # Explicit export accepts the displayed bytes, not a new quality verdict.
        # Keep all observations; never rerun quality gates or replace the chosen version.
        resolved.append((a,v,item))
    folder=root/'deliveries'/('reviewed-'+uuid4().hex[:12]);folder.mkdir(parents=True)
    manifest=[]
    for a,v,item in resolved:
        relative=Path(a['page'])/a['category']/(a['id']+'.'+a['format'])
        target=folder/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(root/v['file'],target)
        manifest.append({'id':a['id'],'name':a['name'],'file':relative.as_posix(),'format':a['format'],
            'size':v['quality']['size'],'source_box':a['box'],'trim_offset':v['trim_offset'],
            'adaptation':a.get('adaptation'),'sha256':digest(target)})
    preview_dir=folder/'previews';preview_dir.mkdir()
    for key in ('icons_png','icons_svg','rasters_png'):
        if key in summary:
            source=(summary_path.parent/summary[key]).resolve()
            if not source.is_relative_to(summary_path.parent):raise ValueError('invalid preview path')
            shutil.copyfile(source,preview_dir/source.name)
    refs=folder/'references';refs.mkdir()
    for source in p['sources']:shutil.copyfile(root/source['file'],refs/(source['id']+'.png'))
    write(folder/'manifest.json',{'assets':manifest,'preview_note':'图标背景透明，绿色等预览底色不属于图标素材。'})
    write(folder/'acceptance.json',{'user_confirmation':evidence,'displayed_report_sha256':digest(summary_path),
        'assets':[{'id':a['id'],'version':v['number'],'sha256':item['sha256']} for a,v,item in resolved]})
    shutil.copyfile(summary_path,folder/'reviewed-snapshot.json')
    write(folder/'processing-report.json',{'assets':[{'id':a['id'],'quality':v['quality'],
        'displayed_findings':item.get('findings',[]),'failure_summary':item.get('failure_summary'),
        'selection_reason':item.get('reason')} for a,v,item in resolved],
        'note':'User requested the displayed effects; original processing observations are retained, not relabeled.'})
    checksums={f.relative_to(folder).as_posix():digest(f) for f in folder.rglob('*') if f.is_file()}
    write(folder/'checksums.json',checksums);archive=folder.with_suffix('.zip')
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        for f in folder.rglob('*'):
            if f.is_file():z.write(f,f.relative_to(folder).as_posix())
    with ZipFile(archive) as z:
        if z.testzip():raise ValueError('ZIP integrity check failed')
        for filename,sha in checksums.items():
            if hashlib.sha256(z.read(filename)).hexdigest()!=sha:raise ValueError('ZIP hash mismatch')
    return {'directory':str(folder),'zip':str(archive),'assets':len(resolved)}
