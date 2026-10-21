import sys,tempfile,unittest,json
from pathlib import Path
from zipfile import ZipFile
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import export_reviewed as er


class ReviewedExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);f=self.root/'versions/icon.png';f.parent.mkdir()
        im=Image.new('RGBA',(8,8));d=ImageDraw.Draw(im);d.rectangle((3,0,4,7),fill='red');d.rectangle((0,3,7,4),fill='red');im.save(f)
        self.version={'file':'versions/icon.png','number':1,'trim_offset':[0,0],
                      'quality':{'size':[8,8],'issues':[],'visual_review':{'verdict':'issues'}}}
        self.asset={'id':'icon','name':'Fixture','selected':True,'format':'png','page':'home',
                    'category':'icon','box':[0,0,8,8],'composition':{'role':'icon'}}
        self.p={'assets':[self.asset],'sources':[]}
        self.report=self.root/'previews/final/report.json';self.report.parent.mkdir(parents=True)
        er.write(self.report,{'kind':'review_only_summary','items':[{'id':'icon','file':'versions/icon.png','sha256':er.digest(f)}]})
        self.evidence={'text':'Export these displayed effects.','message_ref':'human-export','source':'user_message'}
    def run_export(self):
        return er.export(self.root,self.p,self.report,self.evidence,lambda a:self.version,lambda a,v:None)
    def test_explicit_snapshot_export_keeps_visual_assessment_and_verifies_zip(self):
        out=self.run_export()
        self.assertEqual(self.version['quality']['visual_review']['verdict'],'issues')
        with ZipFile(out['zip']) as z:
            self.assertIsNone(z.testzip())
            self.assertEqual(er.digest(self.root/'versions/icon.png'),__import__('hashlib').sha256(z.read('home/icon/icon.png')).hexdigest())
    def test_explicit_export_keeps_technical_findings_without_blocking(self):
        self.version['quality']['issues']=['wrong canvas']
        self.version['quality']['protected_pixels_equal']=False
        out=self.run_export()
        report=json.loads((Path(out['directory'])/'processing-report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['assets'][0]['quality']['issues'],['wrong canvas'])

    def test_changed_displayed_file_still_cannot_be_substituted(self):
        (self.root/'versions/icon.png').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'stale'):self.run_export()
    def test_missing_human_confirmation_cannot_export(self):
        self.evidence={}
        with self.assertRaisesRegex(ValueError,'confirmation'):self.run_export()

    def test_explicit_export_does_not_gate_background_module_cleanup(self):
        self.asset['composition']={'role':'background','surface':{'source_frame':'none',
            'output':'full-bleed','observation':'Clean synthetic source with no UI.',
            'edge_anchors':[[0,7,8,8]],'source_ui':{'observation':'No source modules.','regions':[]}}}
        self.version['quality']['surface_edge_anchors']=[{'box':[0,7,8,8],'pixel_equal':True}]
        self.run_export()
        self.version['quality']['visual_review']['checks']={'module_residue':{
            'verdict':'issues','note':'An old source card remains.'}}
        self.run_export()

    def test_explicit_export_does_not_gate_patterned_corner_review(self):
        corners={'top_left':[0,0,2,2],'top_right':[6,0,8,2],
                 'bottom_left':[0,6,2,8],'bottom_right':[6,6,8,8]}
        self.asset['method']='repair';self.asset['repair_regions']=list(corners.values())
        self.asset['composition']={'role':'background','surface':{'source_frame':'rounded-container',
            'corner_content':'patterned','corner_fill':'generated-blend','corner_regions':corners,
            'output':'full-bleed','observation':'Controlled patterned frame.',
            'edge_anchors':[[2,7,6,8]],'source_ui':{'observation':'No source modules.','regions':[]}}}
        self.version['quality']['surface_edge_anchors']=[{'box':[2,7,6,8],'pixel_equal':True}]
        self.version['quality']['visual_review']['checks']={'module_residue':{'verdict':'passed','note':'No old modules.'}}
        self.run_export()
        self.version['quality']['visual_review']['checks']['corner_texture']={'verdict':'issues','note':'Repeated edge grid.'}
        self.run_export()

    def test_exports_displayed_older_best_candidate_instead_of_latest(self):
        older=dict(self.version)
        self.asset['versions']=[older]
        later=self.root/'versions/later.png';Image.new('RGBA',(8,8),'blue').save(later)
        self.version={**self.version,'number':2,'file':'versions/later.png'}
        out=self.run_export()
        with ZipFile(out['zip']) as z:
            self.assertEqual(z.read('home/icon/icon.png'),(self.root/older['file']).read_bytes())
            self.assertEqual(json.loads(z.read('acceptance.json'))['assets'][0]['version'],1)

    def test_export_does_not_call_quality_verifier(self):
        def reject_quality(*args):raise AssertionError('quality must not gate explicit export')
        er.export(self.root,self.p,self.report,self.evidence,lambda a:self.version,reject_quality)

    def test_displayed_asset_path_cannot_escape_project(self):
        r=json.loads(self.report.read_text(encoding='utf-8'))
        r['items'][0]['file']='../outside.png';er.write(self.report,r)
        with self.assertRaisesRegex(ValueError,'escapes project'):self.run_export()


if __name__=='__main__':unittest.main()
