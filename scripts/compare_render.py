"""Compare a downstream render with a design crop. Font allowances never hide whole panels."""
import argparse
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat
from imaging import load, box_valid


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def max_channel(image):
    red, green, blue = image.convert("RGB").split()
    return ImageChops.lighter(ImageChops.lighter(red, green), blue)


def measure(diff, checked):
    count = checked.histogram()[255]
    if not count:
        raise ValueError("comparison contains no checked pixels")
    histogram = max_channel(diff).histogram(mask=checked)
    cumulative, percentile = 0, 255
    for value, frequency in enumerate(histogram):
        cumulative += frequency
        if cumulative >= math.ceil(count*.99):
            percentile = value
            break
    return {"pixels":count, "mean_rgb_error":round(sum(ImageStat.Stat(diff, checked).mean)/3, 6),
            "p99_max_channel_error":percentile,
            "within_8_fraction":round(sum(histogram[:9])/count, 6),
            "within_16_fraction":round(sum(histogram[:17])/count, 6)}


def passes(metrics, limits):
    return (metrics["mean_rgb_error"] <= limits.get("max_mean_rgb_error", 1)
            and metrics["p99_max_channel_error"] <= limits.get("max_p99_channel_error", 8)
            and metrics["within_8_fraction"] >= limits.get("min_within_8_fraction", .99))


def compare(reference, actual, config, output, render_background=None, ignore_mask=None):
    reference, actual, output = Path(reference), Path(actual), Path(output)
    ref, rendered = load(reference).convert("RGB"), load(actual).convert("RGB")
    if ref.size != rendered.size:
        raise ValueError("reference and render dimensions differ; no automatic alignment or resize allowed")
    backdrop = load(render_background).convert("RGB") if render_background else None
    if backdrop and backdrop.size != ref.size:
        raise ValueError("text-free render background dimensions differ")
    font_mask = Image.new("L", ref.size)
    for window in config.get("font_windows", []):
        box = window["box"]
        if not box_valid(box, ref.size):
            raise ValueError("font window outside comparison")
        mode, threshold = window.get("mode", "dark"), window.get("threshold", 190)
        if mode not in {"dark", "light"} or type(threshold) is not int or not 0 <= threshold <= 255:
            raise ValueError("invalid font window threshold/mode")
        reference_ink = ref.crop(box).convert("L").point(
            lambda v:255 if (v < threshold if mode == "dark" else v > threshold) else 0)
        if backdrop:
            render_ink = max_channel(ImageChops.difference(rendered.crop(box), backdrop.crop(box))).point(lambda v:255 if v>3 else 0)
        else:
            render_ink = rendered.crop(box).convert("L").point(
                lambda v:255 if (v < threshold if mode == "dark" else v > threshold) else 0)
        radius = window.get("expand", 2)
        if type(radius) is not int or not 0 <= radius <= 3:
            raise ValueError("font mask expansion must be 0..3")
        union = ImageChops.lighter(reference_ink, render_ink)
        if radius:
            union = union.filter(ImageFilter.MaxFilter(2*radius+1))
        temporary = Image.new("L",ref.size)
        temporary.paste(union,tuple(box[:2]))
        font_mask = ImageChops.lighter(font_mask, temporary)
    ignored = Image.new("L", ref.size)
    if ignore_mask:
        with Image.open(ignore_mask) as original:
            if original.size != ref.size:
                raise ValueError("ignore mask dimensions differ")
            ignored = original.convert("L").point(lambda v:255 if v else 0)
    for box in config.get("ignore_regions", []):
        if not box_valid(box, ref.size):
            raise ValueError("ignore region outside comparison")
        l,t,r,b = box
        ImageDraw.Draw(ignored).rectangle((l,t,r-1,b-1),fill=255)
    allowance = ImageChops.lighter(font_mask, ignored)
    checked = ImageChops.invert(allowance)
    checked_fraction = checked.histogram()[255]/(ref.width*ref.height)
    minimum = config.get("min_checked_fraction", .7)
    if not .5 <= minimum <= 1 or checked_fraction < minimum:
        raise ValueError("too much of the reference is excluded")
    diff = ImageChops.difference(ref, rendered)
    global_metrics = measure(diff, checked)
    results=[]
    for region in config.get("regions", []):
        box=region["box"]
        if not box_valid(box,ref.size):
            raise ValueError("comparison region outside reference")
        metrics = measure(diff.crop(box),checked.crop(box))
        limits = region.get("limits",config.get("limits",{}))
        results.append({"id":region["id"],"box":box,"metrics":metrics,"limits":limits,"passed":passes(metrics,limits)})
    limits=config.get("limits",{})
    passed=passes(global_metrics,limits) and all(item["passed"] for item in results)
    report={"schema_version":1,"passed":passed,"reference_sha256":sha(reference),"render_sha256":sha(actual),
            "render_background_sha256":sha(render_background) if render_background else None,
            "ignore_mask_sha256":sha(ignore_mask) if ignore_mask else None,
            "size":list(ref.size),"checked_fraction":checked_fraction,"global":global_metrics,"limits":limits,
            "regions":results,"config":config,
            "scope":"Screenshot pixel comparison only. Does not prove hidden pixels, interactions, font layout or other pages."}
    output.mkdir(parents=True,exist_ok=True)
    (output/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    font_mask.save(output/"font-pixel-allowance.png")
    ignored.save(output/"device-allowance.png")
    checked.save(output/"checked-pixels.png")
    amplified=max_channel(diff).point(lambda v:min(255,v*5))
    heat=Image.merge("RGB",(amplified,amplified.point(lambda v:v//3),Image.new("L",ref.size)))
    heat.paste((45,60,70),mask=allowance)
    heat.save(output/"difference-heatmap.png")
    side=Image.new("RGB",(ref.width*3,ref.height))
    side.paste(ref,(0,0));side.paste(rendered,(ref.width,0));side.paste(heat,(ref.width*2,0))
    side.save(output/"comparison.png")
    return report


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference",required=True,type=Path)
    parser.add_argument("--actual",required=True,type=Path)
    parser.add_argument("--config",required=True,type=Path)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--render-background",type=Path)
    parser.add_argument("--ignore-mask",type=Path)
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding="utf-8-sig"))
    report=compare(args.reference,args.actual,config,args.output,args.render_background,args.ignore_mask)
    print(json.dumps({"passed":report["passed"],"global":report["global"]},ensure_ascii=False))
    raise SystemExit(0 if report["passed"] else 1)
