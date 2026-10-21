"""Bounded correction loops and explicitly non-exportable failure diagnostics."""
import sys
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import studio
import analysis_loop
import review_summary
import xml.etree.ElementTree as ET
from unittest.mock import patch
from workflow import REVIEW_CHECKS
import test_quality_gate as fixtures
from fixture_analysis import synthetic_icon_policy, synthetic_inventory


class AnalysisLoopTests(unittest.TestCase):
    setUp=fixtures.QualityGateTests.setUp
    call=fixtures.QualityGateTests.call
    state=fixtures.QualityGateTests.state
    spec=fixtures.QualityGateTests.spec

    def raw(self,*args):
        return studio.run(studio.parser().parse_args(['--project',str(self.root),*args]))

    def check(self,round_no=1,passed=False,asset=None):
        assets=[asset or self.spec()]
        evidence=analysis_loop.template(assets)
        evidence['__inventory__']=synthetic_inventory(self.state(), assets)
        for a in assets:
            item=evidence[a['id']]
            if 'icon_policy' in item:item['icon_policy']=synthetic_icon_policy(a)
            for key in analysis_loop.CHECKS:
                item['checks'][key]={'verdict':'passed','note':'Known source crop checked against intended synthetic geometry.'}
            if not passed:
                item['checks']['source_edges']={'verdict':'unknown','note':f'Round {round_no}: remaining source edge ambiguity after adjustment {round_no}.'}
        studio.write(self.base/'plan.json',assets);studio.write(self.base/'analysis.json',evidence)
        return self.raw('analysis-check','--file',str(self.base/'plan.json'),'--checks-file',str(self.base/'analysis.json'))

    def test_no_generation_without_current_analysis_even_with_valid_plan(self):
        studio.write(self.base/'plan.json',[self.spec()])
        with self.assertRaisesRegex(ValueError,'analysis-check missing'):
            self.raw('manifest','--file',str(self.base/'plan.json'))
        self.assertFalse((self.root/'requests').exists())

    def test_analysis_gate_rejects_missing_source_inventory(self):
        self.check(passed=True)
        evidence=studio.read(self.base/'analysis.json')
        evidence.pop('__inventory__')
        studio.write(self.base/'analysis.json',evidence)
        report=self.raw('analysis-check','--file',str(self.base/'plan.json'),
                        '--checks-file',str(self.base/'analysis.json'))
        self.assertFalse(report['passed'])
        self.assertTrue(any('inventory' in item['issue'] for item in report['technical_errors']))

    def test_old_passed_audit_without_inventory_cannot_execute(self):
        self.check(passed=True)
        p=self.state()
        p['analysis_audit']['rounds'][-1]['evidence'].pop('__inventory__')
        studio.save(self.root,p)
        with self.assertRaisesRegex(ValueError,'inventory'):
            self.raw('manifest','--file',str(self.base/'plan.json'))

    def test_three_checks_then_mark_and_explicit_user_continue(self):
        for i in range(1,4):
            report=self.check(i);self.assertEqual(report['round'],i);self.assertTrue(report['marked'])
            if i<3:
                with self.assertRaises(ValueError):
                    self.raw('allow-analysis','--user-text','Continue','--message-ref','human-message')
        self.assertEqual(self.state()['phase'],'ANALYSIS_REVIEW')
        with self.assertRaisesRegex(ValueError,'exhausted'):
            self.check(4)
        with self.assertRaisesRegex(ValueError,'unresolved'):
            self.raw('manifest','--file',str(self.base/'plan.json'))
        with self.assertRaisesRegex(ValueError,'confirmation evidence'):
            self.raw('allow-analysis')
        self.raw('allow-analysis','--user-text','允许带着已标记的边缘疑点继续生成','--message-ref','human-message')
        self.raw('manifest','--file',str(self.base/'plan.json'))
        self.call('approve-manifest');self.call('process')
        self.assertTrue(self.state()['analysis_audit']['allowance'])
        self.assertTrue(self.raw('status')['assets'])
        self.assertEqual(self.state()['assets'][0]['status'],'awaiting_visual_review')
        with self.assertRaises(ValueError):self.call('preview')
        studio.write(self.base/'generated-checks.json', {k:{'verdict':'passed','note':'Compared the generated fixture to its exact source.'}
                                                       for k in REVIEW_CHECKS})
        with self.assertRaisesRegex(ValueError,'analysis_findings'):
            self.call('visual-review','--asset','asset','--verdict','passed','--note','Checked the generated fixture.',
                      '--checks-file',str(self.base/'generated-checks.json'))

    def test_invalid_pixel_bounds_cannot_be_overridden_as_a_visual_doubt(self):
        for i in range(1,4): self.check(i,asset=self.spec(box=[0,0,33,32]))
        with self.assertRaisesRegex(ValueError,'invalid coordinates'):
            self.raw('allow-analysis','--user-text','Continue','--message-ref','human-message')

    def test_replay_does_not_spend_round_or_reset_budget(self):
        self.check(1)
        with self.assertRaisesRegex(ValueError,'unchanged'):self.check(1)
        self.assertEqual(len(self.state()['analysis_audit']['rounds']),1)

    def test_changed_plan_invalidates_analysis_and_user_changes_open_new_cycle(self):
        self.check(passed=True)
        studio.write(self.base/'plan.json',[self.spec(box=[0,0,30,32])])
        with self.assertRaisesRegex(ValueError,'stale'):
            self.raw('manifest','--file',str(self.base/'plan.json'))
        self.raw('revise-analysis','--user-text','Change boundary','--message-ref','human-change')
        self.assertEqual(self.check(passed=True)['round'],1)
        self.assertEqual(len(self.state()['analysis_history']),1)


class GeneratedFailureTests(unittest.TestCase):
    setUp=fixtures.QualityGateTests.setUp
    call=fixtures.QualityGateTests.call
    state=fixtures.QualityGateTests.state
    asset=fixtures.QualityGateTests.asset
    spec=fixtures.QualityGateTests.spec
    ready=fixtures.QualityGateTests.ready
    task=fixtures.QualityGateTests.task

    def candidates(self):
        self.ready(method='repair',repair_regions=[[0,0,32,32]],
            fidelity_regions=[{'box':[0,0,32,32],'kind':'plain','color':'#faf7ef'}])
        for i,extent in enumerate((4,10),1):
            image=Image.open(self.source).convert('RGBA');ImageDraw.Draw(image).rectangle((5,5,5+extent,5+extent),fill='red')
            file=self.base/f'candidate-{i}.png';image.save(file)
            args=['fulfill','--asset','asset','--file',str(file)]
            if i==2:args+=['--request-id',self.task()['request_id']]
            self.call(*args)

    def select(self,attempt=1):
        studio.write(self.base/'best.json',[{'id':'asset','attempt':attempt,
            'reason':'Compared both candidates: attempt one has a smaller extraneous red region, though both fail.'}])
        return self.call('failed-preview','--file',str(self.base/'best.json'))

    def test_two_attempts_return_marked_best_without_accept_or_export(self):
        self.candidates();before=self.asset();report=self.select()
        self.assertEqual(before['attempts'],2);self.assertFalse(report['can_export'])
        self.assertFalse(report['passed']);self.assertEqual(report['assets'][0]['attempt'],1)
        self.assertTrue((Path(report['directory'])/report['assets'][0]['file']).is_file())
        self.assertEqual(self.asset()['current_version'],before['current_version'])
        for args in [('preview',),('accept','--assets','asset','--accept-differences'),('deliver',)]:
            with self.assertRaises(ValueError):self.call(*args)
        self.assertFalse((self.root/'deliveries').exists())
        self.call('process');self.assertEqual(self.asset()['attempts'],2)

    def summary(self,attempt=1):
        studio.write(self.base/'summary-best.json',[{'id':'asset','attempt':attempt,
            'reason':'Compared both fixture attempts; select the smaller red defect.',
            'failure_summary':'Extraneous red patch remains in the plain source background.'}])
        return self.call('review-summary','--file',str(self.base/'summary-best.json'))

    def test_summary_uses_best_candidate_and_preserves_export_gate(self):
        self.candidates();before=self.asset()
        report=self.summary()
        self.assertEqual(report['items'][0]['attempt'],1)
        self.assertFalse(report['can_export'])
        self.assertTrue(report['items'][0]['findings'])
        self.assertTrue((Path(report['directory'])/report['icons_png']).is_file())
        self.assertEqual(report['display_files'],['icons.png'])
        self.assertTrue((Path(report['directory'])/'display.md').is_file())
        display=(Path(report['directory'])/'display.md').read_text(encoding='utf-8')
        self.assertNotIn('Extraneous red patch',display)
        self.assertNotIn('未通过',display)
        self.assertIn('需要修改哪些素材，还是导出当前效果',display)
        self.assertIn('不属于素材背景',display)
        self.assertEqual(before,self.asset())
        for args in [('preview',),('deliver',)]:
            with self.assertRaises(ValueError):self.call(*args)

    def test_failed_summary_requires_a_visible_failure_description(self):
        self.candidates()
        studio.write(self.base/'incomplete.json',[{'id':'asset','attempt':1,'reason':'Best available.'}])
        with self.assertRaisesRegex(ValueError,'failure_summary'):
            self.call('review-summary','--file',str(self.base/'incomplete.json'))

    def test_final_display_renders_only_selected_best_then_exports_that_version(self):
        self.candidates()
        versions=self.asset()['versions']
        with patch('review_summary.load',wraps=review_summary.load) as load:
            report=self.summary(1)
        self.assertEqual(report['display_policy'],'best-only')
        self.assertEqual(len(report['items']),1)
        chosen=report['items'][0]['file']
        other=next(v['file'] for v in versions if v['file']!=chosen)
        displayed_paths=[Path(call.args[0]).resolve() for call in load.call_args_list]
        self.assertIn((self.root/chosen).resolve(),displayed_paths)
        self.assertNotIn((self.root/other).resolve(),displayed_paths)
        display=(Path(report['directory'])/'display.md').read_text(encoding='utf-8')
        self.assertEqual(display.count('!['),1)
        out=self.call('export-reviewed','--summary',str(Path(report['directory'])/'report.json'),
                      '--user-text','导出','--message-ref','human-export-best')
        from zipfile import ZipFile
        with ZipFile(out['zip']) as archive:
            self.assertEqual(archive.read('home/icon/asset.png'),(self.root/chosen).read_bytes())

    def test_failed_svg_cannot_skip_png_fallback_evaluation(self):
        a={'id':'broken','format':'svg','selected':True,'status':'repair_blocked','attempts':2,
           'generation_cycle':'x','candidate_history':[{'cycle':'x','spec_sha256':studio.signature({})}]}
        with self.assertRaisesRegex(ValueError,'PNG fallback'):
            review_summary.build(self.root,[a],lambda a:{},lambda a:None,lambda a,v:['failed'],lambda a,v:None,
                                 [{'id':'broken','failure_summary':'Emblem shape still differs.'}])

    def test_summary_includes_passed_assets_when_another_asset_failed(self):
        self.candidates()
        # A new, independently checked crop joins the batch without resetting failures.
        plan=[self.spec(method='repair',repair_regions=[[0,0,32,32]],
            fidelity_regions=[{'box':[0,0,32,32],'kind':'plain','color':'#faf7ef'}]),
            self.spec(id='passed')]
        studio.write(self.base/'mixed.json',plan)
        self.call('manifest','--file',str(self.base/'mixed.json'))
        self.call('approve-manifest');self.call('process')
        report=self.summary()
        self.assertEqual(len(report['items']),2)
        self.assertEqual(report['quality_passed'],1)
        self.assertEqual({i['id']:i['passed'] for i in report['items']},{'asset':False,'passed':True})
        self.assertEqual(self.asset()['attempts'],2)

    def test_summary_does_not_fall_back_to_earlier_tampered_candidate(self):
        self.candidates();a=self.asset()
        (self.root/a['candidate_history'][-1]['file']).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash changed'):self.summary(2)

    def test_svg_summary_keeps_vectors_and_namespaces_reused_gradients(self):
        svg=self.base/'icon.svg'
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<defs><linearGradient id="gold"><stop stop-color="#ffd500"/></linearGradient></defs>'
            '<rect width="24" height="24" fill="url(#gold)"/></svg>')
        target=self.base/'gallery.svg'
        review_summary.vector_panel([({'id':str(i),'name':'fixture'},svg,True) for i in range(2)],target)
        elements=list(ET.parse(target).getroot().iter())
        self.assertFalse(any(e.tag.endswith('}image') for e in elements))
        ids=[e.get('id') for e in elements if e.get('id')]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(set(ids),{'item0_gold','item1_gold'})
        self.assertEqual({e.get('fill') for e in elements if e.get('fill','').startswith('url(')},
                         {'url(#item0_gold)','url(#item1_gold)'})

    def test_icon_preview_backing_keeps_dark_and_white_foregrounds_visible(self):
        file=self.base/'render.png'
        Image.new('RGBA',(16,16),'white').save(file)
        self.assertEqual(review_summary.display_backing(file),'#39785e')
        Image.new('RGBA',(16,16),'#337755').save(file)
        self.assertEqual(review_summary.display_backing(file),'#f4f1ea')

    def test_summary_produces_visible_svg_render_and_native_vector_preview(self):
        fixtures.QualityGateTests.vector(self)
        fixtures.QualityGateTests.register(self)
        fixtures.QualityGateTests.visual(self)
        before=self.asset()
        report=self.call('review-summary')
        self.assertEqual(report['display_files'],['icons.png'])
        folder=Path(report['directory'])
        self.assertTrue((folder/report['icons_png']).is_file())
        self.assertTrue((folder/report['icons_svg']).is_file())
        self.assertIn('![icons]',(folder/'display.md').read_text(encoding='utf-8'))
        self.assertEqual(self.asset(),before)

    def test_png_fallback_icon_and_svg_share_one_complete_icon_preview(self):
        svg=fixtures.QualityGateTests.vector(self)
        fixtures.QualityGateTests.register(self);fixtures.QualityGateTests.visual(self)
        studio.write(self.base/'mixed-icons.json',[
            self.spec(format='svg',method='vector',input_file=str(svg)),self.spec(id='png-fallback')])
        self.call('manifest','--file',str(self.base/'mixed-icons.json'));self.call('approve-manifest');self.call('process')
        report=self.call('review-summary')
        self.assertEqual(report['quality_passed'],2)
        self.assertEqual(report['display_files'],['icons.png'])
        self.assertNotIn('rasters_png',report)
        gallery=ET.parse(Path(report['directory'])/report['icons_svg']).getroot()
        self.assertFalse(any(e.tag.endswith('}image') for e in gallery.iter()))
        self.assertEqual(sum(e.tag.endswith('}svg') for e in gallery.iter()),2)

    def test_failure_display_cannot_skip_retries_or_choose_nonexistent_attempt(self):
        self.ready()
        with self.assertRaisesRegex(ValueError,'two exhausted'):self.select()
        # The following is an independent user-requested two-candidate scenario;
        # a manifest change by itself must no longer replenish the budget.
        self.call('revise','--assets','asset','--reason','Start the independent two-candidate fixture scenario.')
        self.candidates()
        with self.assertRaisesRegex(ValueError,'missing'):self.select(3)

    def test_wrong_canvas_attempts_remain_available_for_diagnostics(self):
        self.ready(method='repair',repair_regions=[[10,10,20,20]])
        file=self.base/'large.png';Image.new('RGBA',(80,64),'#faf7ef').save(file)
        for i in range(2):
            args=['fulfill','--asset','asset','--file',str(file)]
            if i:args+=['--request-id',self.task()['request_id']]
            with self.assertRaisesRegex(ValueError,'exact reference'):self.call(*args)
        self.assertFalse(self.asset()['versions']);report=self.select()
        self.assertIn('exact reference',report['assets'][0]['findings'][0])

    def test_candidate_tampering_and_unapproved_third_generation_rejected(self):
        self.candidates();a=self.asset()
        raw=self.root/a['candidate_history'][0]['file'];raw.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'hash changed'):self.select()
        altered=self.spec(method='repair',repair_regions=[[0,0,20,20]])
        studio.write(self.base/'changed.json',[altered])
        with self.assertRaisesRegex(ValueError,'explicit user revise'):
            self.call('manifest','--file',str(self.base/'changed.json'))

    def test_provider_cannot_raise_generation_limit(self):
        self.ready();studio.write(self.base/'provider.json',{'backend':'api','max_attempts':5})
        with self.assertRaisesRegex(ValueError,'fixed at two'):
            self.call('process','--provider',str(self.base/'provider.json'),'--user-text','Use API','--message-ref','human-message')

    def test_internal_plan_correction_preserves_attempt_budget_and_cycle(self):
        self.ready()
        first=self.asset()
        changed=self.spec()
        changed['composition']['decision']='Keep the same source foreground; clarify the extraction observation.'
        studio.write(self.base/'corrected.json',[changed])
        self.call('manifest','--file',str(self.base/'corrected.json'))
        self.assertEqual(self.asset()['attempts'],1)
        self.assertEqual(self.asset()['generation_cycle'],first['generation_cycle'])
        self.call('approve-manifest');self.call('process')
        self.assertEqual(self.asset()['attempts'],2)
        self.assertEqual(self.asset()['generation_cycle'],first['generation_cycle'])

    def test_changed_code_reprocesses_retained_bytes_without_new_generation_or_budget(self):
        self.candidates();before=self.asset()
        with patch('studio.processor_signature',return_value='new-policy'), patch('studio.api_generate',side_effect=AssertionError('No provider allowed')):
            report=self.call('reprocess-candidates','--assets','asset','--user-text','Apply changed rules.',
                             '--message-ref','human-policy-change')
            self.assertEqual(report['generation_calls'],0)
            self.assertEqual(len(report['reprocessed']),2)
            again=self.call('reprocess-candidates','--assets','asset','--user-text','Apply changed rules.',
                            '--message-ref','human-policy-change')
            self.assertEqual(again['reprocessed'],[])
        after=self.asset()
        self.assertEqual(after['attempts'],before['attempts'])
        self.assertEqual(len(after['candidate_history']),len(before['candidate_history']))
        self.assertNotIn('visual_review',studio.current(after)['quality'])

    def test_failed_batch_cannot_accept_an_individually_ready_item(self):
        first=self.spec();second=self.spec(id='other',method='repair',repair_regions=[[0,0,8,8]])
        studio.write(self.base/'batch.json',[first,second])
        self.call('manifest','--file',str(self.base/'batch.json'));self.call('approve-manifest');self.call('process')
        with self.assertRaisesRegex(ValueError,'quality gate'):
            self.call('accept','--assets','asset')
        self.assertEqual(self.asset()['acceptance'],'pending')


if __name__=='__main__':unittest.main()
