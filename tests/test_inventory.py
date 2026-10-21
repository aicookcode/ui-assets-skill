"""Inventory accounts for source occurrences, not just unique output files."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import inventory
import analysis_loop


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.p={'sources':[{'id':'s','size':[100,100]}],
                'scope':{'regions':[{'source':'s','page':'home','box':[0,0,100,90]}]}}
        self.assets=[{'id':'arrow','source':'s','page':'home','category':'icon','box':[10,10,20,20]}]
        self.data={'regions':[{'source':'s','page':'home','box':[0,0,100,90],
                             'observation':'Scanned header, content, controls and navigation.'}],
                   'elements':[{'id':'a','source':'s','page':'home','category':'icon','box':[10,10,20,20],
                                'disposition':'extract','asset_id':'arrow','reason':'Primary arrow.'}],
                   'second_pass':{'verdict':'passed','note':'Rescanned original including small controls.'}}

    def test_extract_reuse_code_and_outside_account_for_occurrences(self):
        shared=dict(self.data['elements'][0],id='b',box=[30,10,40,20],disposition='reuse')
        code=dict(shared,id='c',box=[50,10,60,20],disposition='code',asset_id=None,
                  implementation='CSS plus with two perpendicular strokes.')
        outside=dict(shared,id='d',box=[10,91,20,99],disposition='out-of-scope',asset_id=None)
        self.data['elements'].extend([shared,code,outside])
        self.assertEqual(inventory.validate(self.p,self.assets,self.data),[])

    def test_missing_inventory_cannot_pass(self):
        with self.assertRaisesRegex(ValueError,'required'):inventory.validate(self.p,self.assets,None)

    def test_scanning_only_the_asset_crop_is_not_a_page_scan(self):
        self.data['regions'][0]['box']=[10,10,20,20]
        with self.assertRaisesRegex(ValueError,'every approved scope'):inventory.validate(self.p,self.assets,self.data)

    def test_unassigned_small_icon_is_rejected(self):
        self.data['elements'].append(dict(self.data['elements'][0],id='refresh',disposition='unknown'))
        with self.assertRaisesRegex(ValueError,'disposition'):inventory.validate(self.p,self.assets,self.data)

    def test_in_scope_icon_cannot_be_excluded_as_outside(self):
        self.data['elements'][0].update(disposition='out-of-scope',asset_id=None)
        with self.assertRaisesRegex(ValueError,'hidden'):inventory.validate(self.p,self.assets,self.data)

    def test_missing_asset_and_wrong_source_crop_are_rejected(self):
        for change,pattern in [({'asset_id':'missing'},'missing asset'),({'box':[30,10,40,20]},'bounds')]:
            data=copy.deepcopy(self.data);data['elements'][0].update(change)
            with self.assertRaisesRegex(ValueError,pattern):inventory.validate(self.p,self.assets,data)

    def test_selected_asset_requires_inventory_entry(self):
        self.assets.append(dict(self.assets[0],id='refresh'))
        with self.assertRaisesRegex(ValueError,'missing from'):inventory.validate(self.p,self.assets,self.data)

    def test_second_pass_ambiguity_is_a_finding_not_silent_success(self):
        self.data['second_pass']['verdict']='unknown'
        self.assertEqual(inventory.validate(self.p,self.assets,self.data)[0]['check'],'inventory')

    def test_template_does_not_invent_an_inventory_from_output_assets(self):
        result=analysis_loop.template(self.assets)['__inventory__']
        self.assertEqual(result['elements'],[])
        self.assertEqual(result['second_pass']['verdict'],'unknown')


if __name__=='__main__':unittest.main()
