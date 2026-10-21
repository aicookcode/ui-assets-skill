"""Pixel constraints for declared stable artwork and plain repair areas.

These checks detect measurable drift; they do not count semantic objects or read text.
"""
from PIL import ImageChops, ImageStat
from imaging import box_valid
import re


def validate_regions(asset):
    regions = asset.get("fidelity_regions", [])
    if not isinstance(regions, list):
        raise ValueError("fidelity_regions must be a list")
    size = (asset["box"][2]-asset["box"][0], asset["box"][3]-asset["box"][1])
    if regions and (asset["format"] == "svg" or asset.get("trim") or asset.get("size")):
        raise ValueError("pixel fidelity regions require the unchanged raster reference canvas")
    for region in regions:
        if not isinstance(region, dict) or not box_valid(region.get("box"), size):
            raise ValueError("fidelity region outside reference canvas")
        if region.get("kind") not in {"preserve", "plain"}:
            raise ValueError("fidelity region requires preserve or plain kind")
        if region["kind"] == "plain":
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(region.get("color", ""))):
                raise ValueError("plain fidelity region requires its approved background colour")
            for key, default, low, high in (("max_mean_error", 6, 0, 12),
                                             ("max_outlier_fraction", .01, 0, .05)):
                value = region.get(key, default)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                    raise ValueError("invalid plain fidelity tolerance")


def measure_regions(image, reference, regions):
    issues, measurements = [], []
    for index, region in enumerate(regions):
        crop = image.crop(region["box"])
        if region["kind"] == "preserve":
            equal = ImageChops.difference(crop, reference.crop(region["box"])).getbbox(alpha_only=False) is None
            measurements.append({"region": index, "kind": "preserve", "pixel_equal": equal})
            if not equal:
                issues.append(f"fidelity region {index}: original artwork pixels changed")
        else:
            color = tuple(int(region["color"][n:n+2], 16) for n in (1, 3, 5))
            values = crop.convert("RGB")
            from PIL import Image
            delta = ImageChops.difference(values, Image.new("RGB", values.size, color))
            error = sum(ImageStat.Stat(delta).mean)/3
            pixels = delta.get_flattened_data() if hasattr(delta, "get_flattened_data") else delta.getdata()
            outliers = sum(max(px) > 24 for px in pixels)/(crop.width*crop.height)
            measurements.append({"region": index, "kind": "plain", "mean_color_error": round(error, 4),
                                 "outlier_fraction": round(outliers, 6)})
            if error > region.get("max_mean_error", 6) or outliers > region.get("max_outlier_fraction", .01):
                issues.append(f"fidelity region {index}: palette drift or unexpected marks in plain background")
    return issues, measurements
