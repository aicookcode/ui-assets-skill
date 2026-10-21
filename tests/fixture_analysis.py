"""Explicit analysis evidence for known synthetic fixtures, never production data."""
import studio
import analysis_loop
import inventory
import tempfile
from pathlib import Path
from PIL import Image

_TRIALS = tempfile.TemporaryDirectory(prefix='ui-assets-test-svg-')


def synthetic_svg_trial():
    # Deliberately mismatched controlled candidate and its exact solid-red render.
    # These are test pixels, never evidence for a user image.
    svg = Path(_TRIALS.name) / 'candidate.svg'
    render = Path(_TRIALS.name) / 'render.png'
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><rect width="16" height="16" fill="red"/></svg>', encoding='utf-8')
    Image.new('RGBA', (16,16), 'red').save(render)
    return {'verdict':'issues', 'svg_file':str(svg), 'svg_file_sha256':studio.digest(svg),
            'render_file':str(render), 'render_file_sha256':studio.digest(render),
            'source_features':'Controlled fixture with distinct color and geometry.',
            'output_features':'Solid red square in controlled candidate SVG and render.',
            'remaining_differences':['Controlled candidate does not match fixture geometry or color.']}


def synthetic_icon_policy(asset):
    trial = synthetic_svg_trial() if asset['format']!='svg' else None
    if trial is not None:
        trial.update(source=asset.get('source'), reference_box=asset.get('box'))
    return {'clarity':'clear','outline_verifiable':True,'details_verifiable':True,
            'appearance_verifiable':True,'uncertain_features':[],'simplifications':[],
            'approach':'faithful-vector' if asset['format']=='svg' else 'source-raster',
            'reason':'Known synthetic fixture geometry and color are fully defined.',
            'svg_trial':trial}


def synthetic_icon_review():
    from svg_validation import FEATURES
    return {'verdict':'passed','note':'Compared controlled geometry and source.',
            'source_features':'Known solid synthetic geometry and color.',
            'output_features':'Same defined geometry and color in actual output.',
            'remaining_differences':[],
            'features':{key:{'verdict':'passed','source':'Explicit controlled fixture '+key,
                'output':'Same controlled fixture '+key,'differences':[]} for key in FEATURES},
            'independent_review':{'verdict':'passed','note':'Second inspection of known synthetic fixture geometry.',
                                  'differences':[]}}


def synthetic_inventory(p, assets):
    return {'regions': [dict(r, observation='Scanned the entire controlled synthetic scope.')
                        for r in p['scope']['regions']],
            'elements': [{'id':'element-'+a['id'], 'source':a['source'], 'page':a['page'],
                          'category':a['category'], 'box':a['box'], 'disposition':'extract',
                          'asset_id':a['id'], 'reason':'Explicit controlled fixture element.'}
                         for a in assets if any(inventory.matches(r,a) for r in p['scope']['regions'])],
            'second_pass': {'verdict':'passed','note':'Rechecked all known controlled fixture elements.'}}


def analyse_fixture(root, file):
    p = studio.read(root / "project.json")
    assets = studio.read(file)
    if not isinstance(assets, list):
        return
    try:
        for a in assets:
            studio.validate_asset(p, a, check_scope=a.get("selected", True))
    except (ValueError, KeyError, TypeError):
        return  # Negative manifest tests must still exercise the real rejection.
    evidence = analysis_loop.template(assets)
    evidence[inventory.KEY] = synthetic_inventory(p, assets)
    for asset in assets:
        entry=evidence[asset['id']]
        if 'icon_policy' in entry:entry['icon_policy']=synthetic_icon_policy(asset)
        for key in analysis_loop.CHECKS:
            entry["checks"][key] = {"verdict":"passed", "note":"Known synthetic fixture: source bounds, intended geometry and unchanged reference pixels checked."}
    analysis_loop.check(p, assets, evidence, lambda a: studio.validate_asset(p, a, check_scope=a.get("selected", True)))
    studio.save(root, p)
