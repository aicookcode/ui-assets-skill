"""Check source element accounting against scope and the proposed asset manifest."""
from imaging import box_valid

KEY = '__inventory__'


def template():
    return {'regions': [], 'elements': [],
            'second_pass': {'verdict': 'unknown', 'note': 'Rescan the source independently of the asset list.'}}


def contains(outer, inner):
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def matches(region, element):
    return (region['source'] == element['source'] and
            (not region.get('page') or region['page'] == element['page']) and
            (not region.get('categories') or element['category'] in region['categories']) and
            contains(region['box'], element['box']))


def validate(p, assets, data):
    if not isinstance(data, dict):
        raise ValueError('source inventory is required; rescan the original, not just the asset list')
    regions, elements = data.get('regions'), data.get('elements')
    if not isinstance(regions, list) or not regions or not isinstance(elements, list) or not elements:
        raise ValueError('inventory requires scanned scope regions and source elements')
    scopes = p['scope']['regions']
    for scope in scopes:
        if not any(isinstance(r, dict) and r.get('source') == scope['source'] and
                   r.get('box') == scope['box'] and r.get('page') == scope.get('page') and
                   isinstance(r.get('observation'), str) and r['observation'].strip()
                   for r in regions):
            raise ValueError('inventory must document a scan of every approved scope region')
    sources = {s['id']: s for s in p['sources']}
    planned = {a['id']: a for a in assets}
    ids, extracted, reused = set(), set(), set()
    for e in elements:
        if not isinstance(e, dict):
            raise ValueError('invalid inventory element')
        for field in ('id', 'source', 'page', 'category', 'reason'):
            if not isinstance(e.get(field), str) or not e[field].strip():
                raise ValueError('inventory element requires ' + field)
        if e['id'] in ids:
            raise ValueError('duplicate inventory element ID')
        ids.add(e['id'])
        if e['source'] not in sources or not box_valid(e.get('box'), sources[e['source']]['size']):
            raise ValueError('inventory element source/box is invalid')
        within = any(matches(r, e) for r in scopes)
        disposition = e.get('disposition')
        if disposition == 'out-of-scope':
            if within:
                raise ValueError('in-scope element cannot be hidden as out-of-scope')
            if e.get('asset_id'):
                raise ValueError('out-of-scope element must not target an asset')
        elif disposition in {'extract', 'reuse'}:
            if not within:
                raise ValueError('inventory extraction/reuse lies outside approved scope')
            target = planned.get(e.get('asset_id'))
            if target is None:
                raise ValueError('inventory references a missing asset')
            if target['category'] != e['category']:
                raise ValueError('inventory element and asset category differ')
            if disposition == 'extract':
                if (target['source'] != e['source'] or target['page'] != e['page'] or
                        not contains(target['box'], e['box'])):
                    raise ValueError('inventory extraction must match the asset source, page and bounds')
                extracted.add(target['id'])
            else:
                reused.add(target['id'])
        elif disposition == 'code':
            if not within or e.get('asset_id') or not isinstance(e.get('implementation'), str) or not e['implementation'].strip():
                raise ValueError('code element requires an in-scope implementation description and no asset target')
        else:
            raise ValueError('every inventory element needs extract/reuse/code/out-of-scope disposition')
    if reused - extracted:
        raise ValueError('reused asset requires a primary extraction element')
    if any(a.get('selected', True) and a['id'] not in extracted for a in assets):
        raise ValueError('selected asset is missing from the source inventory')
    review = data.get('second_pass')
    if (not isinstance(review, dict) or review.get('verdict') not in {'passed', 'issues', 'unknown'} or
            not isinstance(review.get('note'), str) or not review['note'].strip()):
        raise ValueError('inventory requires a second source scan verdict and observation')
    return [] if review['verdict'] == 'passed' else [
        {'id': 'batch', 'check': 'inventory', **review}]
