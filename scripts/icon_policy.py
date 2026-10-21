"""Reject unsupported vector reconstruction before asset production."""
from pathlib import Path
import hashlib
from imaging import svg_check, load


def validate_svg_trial(asset, policy):
    trial = policy.get('svg_trial')
    if not isinstance(trial, dict) or trial.get('verdict') != 'issues':
        raise ValueError('raster icon requires a failed SVG trial with actual render evidence')
    if trial.get('source') != asset.get('source') or trial.get('reference_box') != asset.get('box'):
        raise ValueError('SVG trial must identify this source and reference_box')
    for key in ('source_features', 'output_features'):
        if not isinstance(trial.get(key), str) or not trial[key].strip():
            raise ValueError('SVG trial requires observed ' + key)
    differences = trial.get('remaining_differences')
    if not isinstance(differences, list) or not differences or any(
            not isinstance(x, str) or not x.strip() for x in differences):
        raise ValueError('SVG trial requires concrete remaining_differences')
    for field in ('svg_file', 'render_file'):
        path = Path(trial.get(field, ''))
        if not path.is_absolute() or not path.is_file():
            raise ValueError('SVG trial requires an existing absolute ' + field)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if trial.get(field + '_sha256') != actual:
            raise ValueError('SVG trial evidence hash mismatch: ' + field)
        if field == 'svg_file':
            svg_check(path)
        else:
            load(path)


def validate(asset, policy):
    if asset.get('composition',{}).get('role')!='icon':return
    if not isinstance(policy,dict):raise ValueError('icon format requires source clarity and verifiability evidence')
    if policy.get('clarity') not in {'clear','ambiguous','textured'}:
        raise ValueError('icon clarity must be clear, ambiguous or textured')
    for key in ('outline_verifiable','details_verifiable','appearance_verifiable'):
        if type(policy.get(key)) is not bool:raise ValueError('icon policy requires '+key)
    for key in ('uncertain_features','simplifications'):
        if not isinstance(policy.get(key),list) or any(not isinstance(x,str) or not x.strip() for x in policy[key]):
            raise ValueError('icon policy requires explicit '+key)
    if not isinstance(policy.get('reason'),str) or not policy['reason'].strip():
        raise ValueError('icon policy requires an observed format decision reason')
    approach=policy.get('approach')
    if asset['format']=='svg':
        if not policy['outline_verifiable'] or not policy['appearance_verifiable']:
            raise ValueError('unverifiable icon outline/appearance must fall back to PNG, not forced SVG')
        if approach=='faithful-vector':
            if not policy['details_verifiable'] or policy['uncertain_features']:
                raise ValueError('ambiguous icon details cannot be claimed as faithful SVG')
        elif approach=='conservative-vector':
            if not policy['uncertain_features'] or not policy['simplifications']:
                raise ValueError('conservative SVG requires uncertain features and bounded simplifications')
        else:raise ValueError('SVG icon requires faithful-vector or conservative-vector approach')
    elif approach!='source-raster':
        raise ValueError('PNG/WebP icon requires source-raster policy; no invented detail enhancement')
    else:
        validate_svg_trial(asset, policy)


def review(policy, checks):
    entry=checks.get('icon_fidelity',{})
    if not isinstance(entry,dict):raise ValueError('icon review requires icon_fidelity')
    for key in ('source_features','output_features'):
        if not isinstance(entry.get(key),str) or not entry[key].strip():
            raise ValueError('icon_fidelity requires actual '+key)
    remaining=entry.get('remaining_differences')
    if not isinstance(remaining,list):raise ValueError('icon_fidelity requires remaining_differences')
    if entry.get('verdict')=='passed' and remaining:
        raise ValueError('icon passed contradicts unresolved remaining_differences')
    from svg_validation import feature_review
    feature_review(entry)
