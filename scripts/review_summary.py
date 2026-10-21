"""One review-only snapshot: every selected item, one best candidate per item.

This does not accept assets, alter attempts, or weaken the export gate.
"""
from copy import deepcopy
from pathlib import Path
from uuid import uuid4
import hashlib
import re
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw, ImageFont, ImageStat
from imaging import load, svg_check
from workflow import signature

NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def node(tag, **attrs):
    return ET.Element("{" + NS + "}" + tag, {k.replace('_', '-'):str(v) for k,v in attrs.items()})


def display_backing(path):
    im=load(path)
    mean=ImageStat.Stat(im.convert('RGB'),im.getchannel('A')).mean
    luminance=.2126*mean[0]+.7152*mean[1]+.0722*mean[2]
    return '#39785e' if luminance>170 else '#f4f1ea'


def vector_panel(entries, target, backings=None):
    cols = 3
    rows = (len(entries)+cols-1)//cols
    root = node("svg", width=900, height=60+rows*210, viewBox=f"0 0 900 {60+rows*210}")
    root.append(node("rect", width=900, height=60+rows*210, fill="#f4f1ea"))
    title = node("text", x=18, y=30, fill="#444444", font_size=18, font_family="Microsoft YaHei, sans-serif")
    title.text = "图标背景透明；绿色等底色仅用于预览辨识，不属于图标。"
    root.append(title)
    for i, (a, path, passed) in enumerate(entries):
        x, y = (i%cols)*300, 60+(i//cols)*210
        root.append(node("rect", x=x+8, y=y+3, width=284, height=198, rx=8, fill="#ffffff"))
        # A contrasting display backing is outside the icon, never part of its asset.
        backing=(backings or {}).get(a['id'],'#39785e')
        root.append(node("rect", x=x+98, y=y+16, width=104, height=104, rx=6, fill=backing))
        svg_check(path)
        child = deepcopy(ET.parse(path).getroot())
        prefix = f"item{i}_"
        for element in child.iter():
            if "id" in element.attrib:
                element.set("id", prefix+element.get("id"))
            for key,value in list(element.attrib.items()):
                element.set(key, re.sub(r"url\(#([\w-]+)\)", lambda m:"url(#"+prefix+m.group(1)+")",value))
        child.set("x",str(x+110));child.set("y",str(y+28));child.set("width","80");child.set("height","80")
        child.set("preserveAspectRatio","xMidYMid meet")
        root.append(child)
        for dy,text,color in ((145,a["name"],"#292723"),(171,a["id"],"#666666")):
            label = node("text",x=x+150,y=y+dy,text_anchor="middle",font_family="Microsoft YaHei, sans-serif",font_size=15,fill=color)
            label.text=text;root.append(label)
    ET.ElementTree(root).write(target,encoding="utf-8",xml_declaration=True)


def build(root, assets, spec_for, version_for, problems_for, verify, selections=None):
    selected = [a for a in assets if a["selected"]]
    if not selected or any(a["status"] not in {"ready","accepted","repair_blocked","failed"} for a in selected):
        raise ValueError("review-summary requires a settled batch; finish checks/retries first")
    resolved = []
    failed_ids={a['id'] for a in selected if problems_for(a,version_for(a))}
    selections=selections or []
    if (not isinstance(selections,list) or any(not isinstance(s,dict) for s in selections)
        or len(selections)!=len(failed_ids) or {s.get('id') for s in selections}!=failed_ids):
        raise ValueError('select exactly one best candidate for each failed item with comparison reason')
    renders={}
    findings_by_id={}
    for a in selected:
        v = version_for(a)
        passed = not problems_for(a,v)
        if passed:
            verify(a,v)
            path = root/v["file"]
            attempt=a["attempts"]
            findings_by_id[a['id']]=[]
        else:
            candidates=[c for c in a.get("candidate_history",[]) if c["cycle"]==a.get("generation_cycle")
                        and c["spec_sha256"]==signature(spec_for(a))]
            if a["attempts"]<2 or not candidates:
                raise ValueError("failed summary item needs two exhausted attempts and retained candidates")
            choice=next(s for s in selections if s['id']==a['id'])
            if not isinstance(choice.get('failure_summary'),str) or not choice['failure_summary'].strip():
                raise ValueError('failed preview requires a user-readable failure_summary, not only a failed badge')
            if a['format']=='svg':
                fallback=choice.get('raster_fallback',{})
                if fallback.get('decision')!='unavailable' or not isinstance(fallback.get('reason'),str) or not fallback['reason'].strip():
                    raise ValueError('failed SVG must evaluate PNG fallback before final summary; explain if unavailable')
            if type(choice.get('attempt')) is not int or not isinstance(choice.get('reason'),str) or not choice['reason'].strip():
                raise ValueError('best candidate requires attempt and comparison reason')
            matches=[c for c in candidates if c['attempt']==choice['attempt']]
            if len(matches)!=1:raise ValueError('best candidate is missing in the current cycle/specification')
            c=matches[0]
            if not c["readable"]:
                raise ValueError("best candidate is unreadable; report the gap")
            path=root/c["file"]
            if hashlib.sha256(path.read_bytes()).hexdigest()!=c["sha256"]:
                raise ValueError("best candidate hash changed")
            attempt=c["attempt"]
            chosen=next((item for item in a["versions"] if item["number"]==c["version"]),None)
            if chosen:
                verify(a,chosen);path=root/chosen["file"]
            v=chosen
            findings_by_id[a['id']]=problems_for(a,v) if v else [c['error'] or 'No valid processed version']
        if a["format"]=="svg":
            svg_check(path)
            render=v and v['quality'].get('render_preview')
            if not render:raise ValueError('SVG summary requires an actual rendered preview')
            rendered=root/render['file']
            if hashlib.sha256(rendered.read_bytes()).hexdigest()!=render['sha256']:
                raise ValueError('SVG rendered preview hash changed')
            renders[a['id']]=rendered
        else:
            load(path)
            if a.get('composition',{}).get('role')=='icon':renders[a['id']]=path
        resolved.append((a,path,passed,attempt))
    folder=root/"previews"/("review-summary-"+uuid4().hex[:12]);folder.mkdir(parents=True)
    report={"kind":"review_only_summary","can_export":False,"candidate_policy":"best_per_asset",
            "display_policy":"best-only",
            "quality_passed":sum(passed for _,_,passed,_ in resolved),"items":[]}
    for a,path,passed,attempt in resolved:
        report["items"].append({"id":a["id"],"name":a["name"],"format":a["format"],
                                "passed":passed,"attempt":attempt,"can_export":False,
                                "file":str(path.relative_to(root)),
                                "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                                "findings":findings_by_id[a['id']],
                                'failure_summary':next((s['failure_summary'] for s in selections if s['id']==a['id']),None),
                                "reason":next((s['reason'] for s in selections if s['id']==a['id']),
                                              'Current quality-passed version')})
    font_path=Path("C:/Windows/Fonts/msyh.ttc")
    font=ImageFont.truetype(str(font_path),18) if font_path.is_file() else ImageFont.load_default()
    entries=[(a,path,passed) for a,path,passed,_ in resolved
             if a['format']=='svg' or a.get('composition',{}).get('role')=='icon']
    if entries:
        backings={a['id']:(a.get('matte_background') or display_backing(renders[a['id']])) for a,_,_ in entries}
        vectors=[entry for entry in entries if entry[0]['format']=='svg']
        if vectors:
            vector_panel(vectors,folder/'icons.svg',backings);report['icons_svg']='icons.svg'
        board=Image.new('RGB',(900,60+((len(entries)+2)//3)*210),'#f4f1ea');draw=ImageDraw.Draw(board)
        draw.text((18,18),'图标背景透明；绿色等底色仅用于预览辨识，不属于图标。',font=font,fill='#444444')
        for i,(a,path,passed) in enumerate(entries):
            x=(i%3)*300;y=60+(i//3)*210
            draw.rectangle((x+8,y+3,x+292,y+201),fill='white')
            draw.rectangle((x+98,y+16,x+202,y+120),fill=backings[a['id']])
            im=load(renders[a['id']]);im.thumbnail((80,80))
            if max(im.size)<80:
                scale=80/max(im.size)
                im=im.resize((round(im.width*scale),round(im.height*scale)),Image.Resampling.LANCZOS)
            board.paste(im,(x+150-im.width//2,y+68-im.height//2),im)
            for dy,label,color in ((134,a['name'],'#292723'),(159,a['id']+' · '+a['format'].upper(),'#666666')):
                draw.text((x+150,y+dy),label,font=font,fill=color,anchor='mt')
        board.save(folder/'icons.png');report['icons_png']='icons.png'
    rasters=[item for item in resolved if item[0]["format"]!="svg" and item[0].get('composition',{}).get('role')!='icon']
    if rasters:
        wrapped={}
        for a,_,ok,_ in rasters:
            value=''
            lines=[];line=''
            for char in value:
                if font.getlength(line+char)>500:
                    lines.append(line);line=''
                line+=char
            if line:lines.append(line)
            wrapped[a['id']]=lines
        cols=1 if len(rasters)==1 else 2
        preview_height=min(940,load(rasters[0][1]).height) if cols==1 else 235
        row_height=max(330,preview_height+95,max((330+23*len(x) for x in wrapped.values()),default=330))
        height=50+((len(rasters)+cols-1)//cols)*row_height
        board=Image.new("RGB",(550*cols,height),"#f2eee7");draw=ImageDraw.Draw(board)
        draw.text((18,12),"位图最终效果",font=font,fill="#444444")
        for i,(a,path,passed,attempt) in enumerate(rasters):
            x=(i%cols)*550;y=50+(i//cols)*row_height
            draw.rectangle((x+8,y+5,x+542,y+row_height-9),fill="white")
            draw.text((x+20,y+14),a["name"],font=font,fill="#292723")
            draw.text((x+20,y+42),a['id'],font=font,fill='#666666')
            image=load(path);image.thumbnail((500,preview_height))
            board.paste(image,(x+(550-image.width)//2,y+76+(preview_height-image.height)//2),image)
            for line_no,line in enumerate(wrapped[a['id']]):
                draw.text((x+20,y+315+23*line_no),line,font=font,fill='#b00020')
        board.save(folder/"rasters.png");report["rasters_png"]="rasters.png"
    report['display_files']=[report[k] for k in ('rasters_png','icons_png') if k in report]
    lines=['以下每项仅展示已挑选的最优结果，不展示两轮对比或其他候选。',
           '图标背景透明；绿色等预览底色仅用于看清图标，不属于素材背景。','']
    lines.extend(f'![{Path(f).stem}]({(folder/f).resolve().as_posix()})' for f in report['display_files'])
    if 'icons_svg' in report:lines.append(f'[SVG 矢量预览]({(folder/report["icons_svg"]).resolve().as_posix()})')
    lines.append('需要修改哪些素材，还是导出当前效果？')
    (folder/'display.md').write_text('\n\n'.join(lines),encoding='utf-8')
    return folder,report
