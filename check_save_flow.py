"""Offline regression for option order, inherited prices, timing and verification."""
import copy
import tempfile
from unittest.mock import patch
from engine import fingerprint, product_key, snapshot, variation_order
from service import Etsy, Service
from storage import Store


class FakeEtsy(Etsy):
    def __init__(self, service, shop_id):
        self.service, self.shop_id = service, shop_id
        self.rows = {str(l['listing_id']):copy.deepcopy(l) for l in service.store.all('listing')}
        self.calls = []
        self.reorder = False

    def request(self, path, method='GET', data=None, form=False):
        lid = path.split('/listings/')[1].split('/')[0].split('?')[0]
        self.last_request_timing = {'http':.02,'wait':.01}
        self.calls.append((lid,method,copy.deepcopy(self.service.status.get('progress'))))
        row = self.rows[lid]
        if method == 'PUT':
            row['snapshot']['inventory'] = copy.deepcopy(data)
            if self.reorder:
                row['snapshot']['inventory']['products'].sort(key=product_key)
            return {}
        if '/inventory' in path:
            return copy.deepcopy(row['snapshot']['inventory'])
        return copy.deepcopy(row)


def fixture(service):
    service.sample()
    rows = service.store.all('listing')[:2]
    order = ['Z Hoodie / 2XL','A Tee / XL','A Tee / S']
    for row in rows:
        template = copy.deepcopy(row['snapshot']['inventory']['products'][0])
        products = []
        for style,price in zip(order,[50,32,20]):
            for color in ['Pepper','Ivory']:
                p = copy.deepcopy(template)
                p['property_values'][0]['values'] = [style]
                p['property_values'][1]['values'] = [color]
                p['offerings'][0]['price'] = price
                products.append(p)
        row['snapshot'] = snapshot(row,{**row['snapshot']['inventory'],'products':products})
        service.store.put('listing',row['listing_id'],row)
        assert variation_order(row['snapshot']['inventory'])['513'] == order
    return rows,order


def main():
    with tempfile.TemporaryDirectory() as directory:
        service = Service(Store(directory),'https://localhost:8765/oauth/callback')
        rows,order = fixture(service)
        ids = [str(r['listing_id']) for r in rows]
        base = {'kind':'option','mode':'copy','auto_source':True,'property_id':'514','old':'Pepper','value':'Lilac','quantity':999,'filters':{}}
        batch = service.build_review({'shop_id':'sample','listing_ids':ids,'rules':[base]})
        for item in batch['items']:
            assert item['status']=='ready',item.get('error')
            before = item['before']['inventory']['products']
            after = item['after']['inventory']['products']
            assert after[:len(before)] == before
            new = after[len(before):]
            assert [p['offerings'][0]['price'] for p in new] == [50,32,20]
            assert all(p['offerings'][0]['quantity']==999 for p in new)
        override = service.build_review({'shop_id':'sample','listing_ids':ids,'rules':[{**base,'price':77}]})
        assert all(p['offerings'][0]['price']==77 for p in override['items'][0]['after']['inventory']['products'][6:])
        rules = [{**base,'property_id':'513','old':order[0],'value':'Youth Long Sleeve '+size,'price':25} for size in ['S','M','L']]
        batch = service.build_review({'shop_id':'sample','listing_ids':ids,'rules':rules})
        expected = order + ['Youth Long Sleeve '+size for size in ['S','M','L']]
        for item in batch['items']:
            assert variation_order(item['after']['inventory'])['513']==expected
        shuffled = copy.deepcopy(batch['items'][0]['after'])
        shuffled['inventory']['products'].sort(key=product_key)
        assert fingerprint(shuffled)==fingerprint(batch['items'][0]['after'])
        assert fingerprint(shuffled,include_order=True)!=fingerprint(batch['items'][0]['after'],include_order=True)
        shop = service.shop('sample');shop['sample']=False;service.store.put('shop','sample',shop)
        batch['sample']=False;service.store.put('batch',batch['id'],batch)
        api = FakeEtsy(service,'sample')
        with patch('service.Etsy',return_value=api):
            service.apply_batch(batch['id'],ids)
        saved = service.batch(batch['id'])
        assert saved['status']=='complete'
        assert len(api.calls)==10
        assert all(variation_order(i['verified']['inventory'])['513']==expected for i in saved['items'])
        assert abs(saved['timing']['etsy_read']-.16)<1e-8
        assert abs(saved['timing']['etsy_write']-.04)<1e-8
        assert abs(saved['timing']['rate_wait']-.10)<1e-8
        assert saved['timing']['local_save']>0
        assert [step['step'] for step in saved['items'][0]['timings']]==['Read product from Etsy','Read variations from Etsy','Save variations to Etsy','Verify product from Etsy','Verify variations from Etsy']
        assert any(c[2]['completed']==1 and c[2]['total']==2 for c in api.calls if c[0]==ids[1])
        # A successful write with the wrong returned order must not count as verified.
        rows,order = fixture(service)
        bad = service.build_review({'shop_id':'sample','listing_ids':[ids[0]],'rules':rules})
        shop=service.shop('sample');shop['sample']=False;service.store.put('shop','sample',shop)
        bad['sample']=False;service.store.put('batch',bad['id'],bad)
        api=FakeEtsy(service,'sample');api.reorder=True
        with patch('service.Etsy',return_value=api):
            service.apply_batch(bad['id'],ids)
        result=service.batch(bad['id'])
        assert result['items'][0]['status']=='uncertain'
        assert 'different option order' in result['items'][0]['error']
    print('Save-flow check passed: existing and S/M/L order, inherited/explicit color prices, stock 999, separate step timings, progress and order verification.')


if __name__=='__main__':
    main()
