"""Release and recovery behavior on synthetic, independently checkable images."""
from pathlib import Path
import json
import sys
import tempfile
import unittest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"scripts"))
import studio
from fixture_analysis import analyse_fixture,synthetic_icon_review
from workflow import REVIEW_CHECKS


class QualityGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'project'
        self.source = self.base / 'source.png'
        Image.new('RGBA', (32,32), '#faf7ef').save(self.source)
        self.call('init','--mode','B','--sources',str(self.source))
        self.call('scope','--all')

    def call(self, *args):
        args=list(args)
        if args[0] == 'manifest':
            analyse_fixture(self.root, Path(args[args.index('--file')+1]))
        if args[0]=='fulfill' and '--reference-sha256' not in args:
            args += ['--reference-sha256',self.task()['reference_crop_sha256']]
        if args[0] in {'scope','approve-manifest','accept','revise','restore'}:
            args += ['--user-text','Explicit instruction for the synthetic fixture.','--message-ref','fixture-approval']
        return studio.run(studio.parser().parse_args(['--project',str(self.root),*args]))

    def state(self): return studio.read(self.root/'project.json')
    def asset(self): return self.state()['assets'][0]
    def spec(self, **kw):
        return {'id':'asset','name':'fixture','page':'home','category':'icon','source':'source-1',
            'box':[0,0,32,32],'format':'png','method':'crop',
            'composition':{'role':'icon','code_reproducible':False,'decision':'Keep exact original pixels.','embedded_ui':[]}, **kw}

    def ready(self, **kw):
        studio.write(self.base/'manifest.json',[self.spec(**kw)])
        self.call('manifest','--file',str(self.base/'manifest.json'))
        self.call('approve-manifest'); self.call('process')

    def visual(self, verdict='passed', failing='details'):
        a=self.asset()
        keys=set(REVIEW_CHECKS)
        if a.get('repair_regions'): keys.update(('repair_boundary','major_objects'))
        if a['composition']['role'] in {'background','illustration'}: keys.add('composition')
        checks={k:{'verdict':'passed','note':'Compared the controlled fixture to its original.'} for k in keys}
        if studio.current(a)['quality'].get('icon_policy'):checks['icon_fidelity']=synthetic_icon_review()
        evidence=studio.current(a)['quality'].get('source_comparison')
        if evidence:checks['icon_fidelity']['comparison_sha256']=evidence['sha256']
        if verdict!='passed': checks[failing]={'verdict':verdict,'note':'Unexpected mark detected in the synthetic candidate.'}
        studio.write(self.base/'checks.json',checks)
        return self.call('visual-review','--asset','asset','--verdict',verdict,'--note','Controlled reference/candidate comparison.',
                         '--checks-file',str(self.base/'checks.json'))

    def task(self): return studio.read(self.root/'requests/asset.json')

    def vector(self):
        file=self.base/'icon.svg'
        file.write_text('<svg viewBox="0 0 32 32"><rect width="32" height="32" fill="#faf7ef"/></svg>')
        self.ready(format='svg',method='vector',input_file=str(file))
        return file

    def register(self):
        a=self.asset();v=studio.current(a)
        self.call('render-preview','--asset','asset','--version',str(v['number']),
                  '--file',str(self.source),'--vector-sha256',studio.digest(self.root/v['file']))

    def test_waiting_native_is_not_user_review(self):
        self.ready(method='repair',repair_regions=[[10,10,20,20]])
        self.assertEqual(self.state()['phase'],'ASSET_PROCESSING')
        with self.assertRaisesRegex(ValueError,'quality gate'): self.call('preview')
        self.assertFalse((self.root/'previews/results').exists())
        self.assertTrue((self.root/'inspections/results').exists())

    def test_svg_requires_actual_render_before_visual_pass_and_acceptance(self):
        self.vector()
        self.assertEqual(self.state()['phase'],'QUALITY_VALIDATION')
        with self.assertRaisesRegex(ValueError,'rendered preview'): self.visual()
        with self.assertRaises(ValueError): self.call('accept','--assets','asset','--accept-differences')
        self.register();self.visual();self.call('preview')
        self.assertEqual(self.state()['phase'],'RESULT_REVIEW')
        self.call('accept','--assets','asset')
        delivery=self.call('deliver')
        self.assertTrue((Path(delivery['directory'])/'home/icon/asset/render-preview.png').is_file())

    def test_svg_preview_wrong_hash_version_and_empty_rejected(self):
        self.vector();a=self.asset();v=studio.current(a)
        args=['render-preview','--asset','asset','--version','1','--file',str(self.source),'--vector-sha256']
        with self.assertRaisesRegex(ValueError,'hash'): self.call(*args,'wrong')
        empty=self.base/'empty.png';Image.new('RGBA',(32,32),(0,0,0,0)).save(empty)
        with self.assertRaisesRegex(ValueError,'empty'):
            self.call('render-preview','--asset','asset','--version','1','--file',str(empty),
                      '--vector-sha256',studio.digest(self.root/v['file']))
        with self.assertRaisesRegex(ValueError,'current SVG version'):
            self.call('render-preview','--asset','asset','--version','2','--file',str(self.source),'--vector-sha256','wrong')

    def test_svg_render_replacement_invalidates_previous_visual_pass(self):
        self.vector();self.register();self.visual();self.register()
        with self.assertRaisesRegex(ValueError,'quality gate'): self.call('preview')

    def test_opaque_svg_cannot_claim_transparency_through_static_validation(self):
        file=self.base/'opaque.svg'
        file.write_text('<svg viewBox="0 0 32 32"><rect width="32" height="32" fill="red"/></svg>')
        self.ready(format='svg',method='vector',input_file=str(file),transparent=True)
        self.register()
        self.assertEqual(self.asset()['status'],'awaiting_repair')
        self.assertEqual(self.task()['kind'],'vector_revision')
        self.assertIn('transparency required',self.task()['prompt'])
        with self.assertRaisesRegex(ValueError,'technical'): self.visual()

    def test_modified_svg_preview_cannot_be_approved(self):
        self.vector();self.register();self.visual()
        q=studio.current(self.asset())['quality']
        Image.new('RGBA',(32,32),'red').save(self.root/q['render_preview']['file'])
        with self.assertRaisesRegex(ValueError,'stale'): self.call('preview')

    def test_visual_failure_queues_actionable_repair_without_user_roundtrip(self):
        self.ready(); self.visual('issues','new_elements')
        a=self.asset();task=self.task()
        self.assertEqual(a['status'],'awaiting_repair')
        self.assertEqual(task['kind'],'asset_repair')
        self.assertIn('new_elements',task['prompt'])
        self.assertTrue(Path(task['reference_crop']).is_file())
        self.assertEqual(task['remaining_attempts'],1)
        with self.assertRaisesRegex(ValueError,'quality gate'): self.call('preview')
        self.assertEqual(len(self.call('repair-queue')['tasks']),1)

    def test_repeat_process_does_not_reset_budget_or_duplicate_repair(self):
        self.ready();self.visual('issues');task=self.task()
        for _ in range(3): self.call('process')
        self.assertEqual(self.task()['request_id'],task['request_id'])
        self.assertEqual(self.asset()['attempts'],1)
        self.assertEqual(len(self.asset()['versions']),1)

    def test_repaired_result_requires_fresh_inspection_and_preserves_history(self):
        self.ready();self.visual('issues');task=self.task()
        self.call('fulfill','--asset','asset','--file',str(self.source),'--request-id',task['request_id'])
        self.assertEqual([v['number'] for v in self.asset()['versions']],[1,2])
        self.assertNotIn('visual_review',studio.current(self.asset())['quality'])
        self.call('preview')  # Exact original crop needs no invented semantic judgement.

    def test_wrong_repair_request_id_cannot_spend_an_attempt(self):
        self.ready();self.visual('issues')
        with self.assertRaisesRegex(ValueError,'request_id'):
            self.call('fulfill','--asset','asset','--file',str(self.source),'--request-id','stale')
        self.assertEqual(self.asset()['attempts'],1)

    def test_wrong_reference_crop_hash_is_rejected_before_spending_attempt(self):
        self.ready(method='repair',repair_regions=[[10,10,20,20]])
        with self.assertRaisesRegex(ValueError,'exact crop'):
            self.call('fulfill','--asset','asset','--file',str(self.source),'--reference-sha256','whole-page-or-wrong-crop')
        self.assertEqual(self.asset()['attempts'],0)

    def test_exhausted_repair_cannot_reach_preview_or_accept_differences(self):
        self.ready();self.visual('issues');task=self.task()
        self.call('fulfill','--asset','asset','--file',str(self.source),'--request-id',task['request_id'])
        self.visual('issues','seams')
        self.assertEqual(self.state()['phase'],'REPAIR_BLOCKED')
        self.assertEqual(self.asset()['attempts'],2)
        self.call('process')
        self.assertEqual(self.asset()['attempts'],2)
        self.assertEqual(self.call('repair-queue')['blocked'],['asset'])
        with self.assertRaises(ValueError): self.call('accept','--assets','asset','--accept-differences')
        with self.assertRaisesRegex(ValueError,'quality gate'): self.call('preview')

    def test_unknown_visual_verdict_stays_internal(self):
        self.ready();self.visual('unknown')
        self.assertEqual(self.asset()['status'],'awaiting_repair')
        with self.assertRaisesRegex(ValueError,'quality gate'): self.call('preview')
        self.assertEqual(len(self.call('repair-queue')['tasks']),1)

    def test_extra_coloured_mark_in_declared_plain_region_is_automatically_blocked(self):
        self.ready(method='repair',repair_regions=[[0,0,32,32]],
                   fidelity_regions=[{'box':[0,0,32,32],'kind':'plain','color':'#faf7ef'}])
        im=Image.open(self.source).convert('RGBA');ImageDraw.Draw(im).rectangle((10,10,16,16),fill='#f18cba')
        candidate=self.base/'candidate.png';im.save(candidate)
        self.call('fulfill','--asset','asset','--file',str(candidate))
        self.assertEqual(self.asset()['status'],'awaiting_repair')
        self.assertIn('palette drift',self.task()['prompt'])
        with self.assertRaisesRegex(ValueError,'technical'):self.visual()

    def test_explicit_preserve_region_detects_overly_broad_repair_mask(self):
        self.ready(method='repair',repair_regions=[[0,0,32,32]],
                   fidelity_regions=[{'box':[0,0,8,8],'kind':'preserve'}])
        candidate=self.base/'candidate.png';Image.new('RGBA',(32,32),'red').save(candidate)
        self.call('fulfill','--asset','asset','--file',str(candidate))
        self.assertIn('original artwork pixels changed',self.task()['prompt'])

    def test_old_visual_checklist_does_not_unlock_review(self):
        self.vector();self.register();self.visual()
        p=self.state();review=studio.current(p['assets'][0])['quality']['visual_review']
        review['checks'].pop('new_elements');studio.save(self.root,p)
        with self.assertRaisesRegex(ValueError,'obsolete'):self.call('preview')

    def test_failed_item_prevents_partial_batch_review(self):
        self.ready();p=self.state();second=self.spec(id='other',method='repair',repair_regions=[[0,0,4,4]])
        studio.write(self.base/'manifest.json',[self.spec(),second]);self.call('manifest','--file',str(self.base/'manifest.json'))
        self.call('approve-manifest');self.call('process')
        with self.assertRaisesRegex(ValueError,'quality gate'):self.call('preview')

    def test_legacy_pass_cannot_skip_source_comparison(self):
        self.vector();self.register();self.visual()
        p=self.state();studio.current(p['assets'][0])['quality'].pop('source_comparison')
        studio.save(self.root,p)
        with self.assertRaisesRegex(ValueError,'source comparison'):self.call('preview')

    def test_changed_comparison_blocks_qualified_release(self):
        self.vector();self.register();self.visual()
        comparison=studio.current(self.asset())['quality']['source_comparison']
        (self.root/comparison['file']).write_bytes(b'changed comparison')
        with self.assertRaisesRegex(ValueError,'comparison evidence is stale'):self.call('preview')


if __name__=='__main__':unittest.main()
