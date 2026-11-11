"""Offline regression for named option placement, preview, save and restore."""
import copy
import tempfile
from unittest.mock import patch
from engine import ReviewError, fingerprint, preview, product_key, variation_order
from service import Service
from storage import Store
from check_save_flow import FakeEtsy, fixture


def main():
    with tempfile.TemporaryDirectory() as directory:
        service=Service(Store(directory),'https://localhost:8765/oauth/callback')
        rows,_=fixture(service)
        original=['Youth Tee / S','Youth Tee / M','Adult Hoodie / 2XL']
        for n,row in enumerate(rows):
            products=row['snapshot']['inventory']['products']
            for p in products:
                p['property_values'][0]['values']=[original[['Z Hoodie / 2XL','A Tee / XL','A Tee / S'].index(p['property_values'][0]['values'][0])]]
            if n:
                products[:]=[p for p in products if p['property_values'][1]['values']==['Ivory']]
            service.store.put('listing',row['listing_id'],row)
        ids=[str(r['listing_id']) for r in rows]
        added=['Youth Long Sleeve '+size for size in ['S','M','L']]
        additions=[{'kind':'option','mode':'copy','auto_source':True,'property_id':'513','old':original[-1],'value':name,'price':25,'quantity':999,'filters':{}} for name in added]
        moves=[{'value':added[0],'anchor':original[-1],'position':'before'},{'value':added[1],'anchor':added[0],'position':'after'},{'value':added[2],'anchor':added[1],'position':'after'}]
        order={'kind':'order','property_id':'513','moves':moves}
        expected=original[:2]+added+original[2:]
        batch=service.build_review({'shop_id':'sample','listing_ids':ids,'rules':[order]+additions})
        for item in batch['items']:
            assert item['status']=='ready',item.get('error')
            old={product_key(p):p for p in item['before']['inventory']['products']}
            new={product_key(p):p for p in item['after']['inventory']['products']}
            assert all(new[k]==p for k,p in old.items())
            assert variation_order(item['after']['inventory'])['513']==expected
            assert variation_order(item['before']['inventory'])['514']==variation_order(item['after']['inventory'])['514']
            change=next(c for c in item['changes'] if c.get('kind')=='order')
            assert change['before']==original and change['after']==expected
        shop=service.shop('sample');shop['sample']=False;service.store.put('shop','sample',shop)
        api=FakeEtsy(service,'sample')
        with patch('service.Etsy',return_value=api):service.apply_batch(batch['id'],ids)
        assert service.batch(batch['id'])['status']=='complete'
        # Move a previously saved size with no addition or value changes.
        only_order=service.build_review({'shop_id':'sample','listing_ids':ids,'rules':[{'kind':'order','property_id':'513','moves':[{'value':added[2],'anchor':original[0],'position':'before'}]}]})
        for item in only_order['items']:
            assert item['status']=='ready'
            assert fingerprint(item['before'])==fingerprint(item['after'])
            assert fingerprint(item['before'],include_order=True)!=fingerprint(item['after'],include_order=True)
            assert len(item['changes'])==1 and item['changes'][0]['kind']=='order'
        api=FakeEtsy(service,'sample')
        with patch('service.Etsy',return_value=api):service.apply_batch(only_order['id'])
        assert sum(method=='PUT' for _,method,_ in api.calls)==2
        restore=service.restore_review(only_order['id'])
        api=FakeEtsy(service,'sample')
        with patch('service.Etsy',return_value=api):service.apply_batch(restore['id'])
        assert service.batch(restore['id'])['status']=='complete'
        for lid in ids:assert variation_order(service.store.get('listing',lid)['snapshot']['inventory'])['513']==expected
        colors=service.build_review({'shop_id':'sample','listing_ids':[ids[0]],'rules':[{'kind':'order','property_id':'514','moves':[{'value':'Ivory','anchor':'Pepper','position':'before'}]}]})
        assert colors['items'][0]['status']=='ready'
        assert variation_order(colors['items'][0]['after']['inventory'])['514']==['Ivory','Pepper']
        assert fingerprint(colors['items'][0]['before'])==fingerprint(colors['items'][0]['after'])
        missing=copy.deepcopy(batch['items'][0]['before'])
        missing['inventory']['products']=[p for p in missing['inventory']['products'] if p['property_values'][0]['values']!=[original[-1]]]
        try:
            preview(missing,[{'kind':'order','property_id':'513','moves':[{'value':original[0],'anchor':original[-1],'position':'after'}]}])
            raise AssertionError('Missing anchor was silently substituted')
        except ReviewError as error:assert 'both options' in str(error)
    print('Option-order check passed: insert S/M/L beside Youth options, preserve rows/colors, preview order, reorder-only save, restore and missing-anchor protection.')


if __name__=='__main__':main()
