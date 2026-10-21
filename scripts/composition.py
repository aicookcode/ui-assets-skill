"""Machine-checkable composition decisions; semantic inspection is still required."""
import re


def validate_blending(asset, size):
    """Only declared plain bands may be blended; artwork is never resized here."""
    join=asset.get("background_join")
    if join is not None:
        if not isinstance(join,dict):
            raise ValueError("invalid background join")
        start,transition=join.get("start_y"),join.get("transition")
        if (type(start) is not int or type(transition) is not int or
            not 0<start<size[1] or not 1<=transition<=size[1]-start or
            join.get("pattern_free") is not True or
            not re.fullmatch(r"#[0-9a-fA-F]{6}",str(join.get("base_color","")))):
            raise ValueError("background join requires a declared pattern-free band and valid dimensions/colour")
    blends=asset.get("edge_blend",[])
    if not isinstance(blends,list):
        raise ValueError("edge_blend requires a list")
    for blend in blends:
        if (not isinstance(blend,dict) or blend.get("edge") not in {"top","bottom"} or
            type(blend.get("pixels")) is not int or not 1<=blend["pixels"]<=size[1] or
            blend.get("pattern_free") is not True or
            not re.fullmatch(r"#[0-9a-fA-F]{6}",str(blend.get("color","")))):
            raise ValueError("edge blending requires a declared pattern-free band and valid colour")


def validate_composition(asset, required=False):
    c=asset.get("composition")
    if c is None:
        if required:
            raise ValueError("composition analysis is required before processing; reimport an analysed manifest")
        return  # Legacy data remains readable, but execution requires analysis.
    if not isinstance(c,dict) or c.get("role") not in {"background","illustration","icon","texture"}:
        raise ValueError("composition requires a recognised role")
    if type(c.get("code_reproducible")) is not bool or not isinstance(c.get("decision"),str) or not c["decision"].strip():
        raise ValueError("composition requires code_reproducible and a concrete decision")
    if c["code_reproducible"] and asset["format"]!="svg":
        raise ValueError("code-reproducible structure must not be exported as a raster")
    if not isinstance(c.get("embedded_ui"),list) or c["embedded_ui"]:
        raise ValueError("detach embedded controls/icons/text from decorative assets")
    if asset.get("method") == "generate":
        reasons = c.get("fallback_reasons", {})
        if not isinstance(reasons, dict) or any(
                not isinstance(reasons.get(method), str) or not reasons[method].strip()
                for method in ("reuse", "crop", "vector", "repair")):
            raise ValueError("reference generation requires fallback_reasons for reuse/crop/vector/repair")
    if c["role"]=="background":
        if c.get("layout_independent") is not True:
            raise ValueError("background must be independent of business-module positions")
        bg=c.get("background",{})
        if bg.get("pattern") not in {"top-only","bottom-only","both","none"}:
            raise ValueError("background requires pattern distribution analysis")
        if bg.get("strategy") not in {"full-base","split-ends","whole-proportional","code-base"}:
            raise ValueError("background requires an explicit composition strategy")
        if bg["pattern"] in {"top-only","bottom-only"} and bg["strategy"]!="full-base":
            raise ValueError("single-ended artwork requires the full base and its colour transition")
        if bg.get("scale") not in {"uniform","tile","solid-extension"}:
            raise ValueError("background must not use nonuniform stretching")
        if bg["pattern"]!="none" and bg["scale"]=="tile":
            raise ValueError("scenery must not be tiled; only seamless plain texture may repeat")
        if bg["pattern"]=="top-only" and bg.get("anchor")!="top":
            raise ValueError("top-only background requires top anchor")
        if bg["pattern"]=="bottom-only" and bg.get("anchor")!="bottom":
            raise ValueError("bottom-only background requires bottom anchor")
        if not re.fullmatch(r"#[0-9a-fA-F]{6}",bg.get("base_color","")):
            raise ValueError("background requires its merge base colour")
        if bg["pattern"]=="both" and not bg.get("height_analysis"):
            raise ValueError("two-ended background requires height analysis before choosing whole/split")
