"""Offline shop sync regression; never contacts Etsy."""
import copy
import tempfile
import urllib.parse
from unittest.mock import patch
from service import EtsyError, Service
from engine import ReviewError
from storage import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        service = Service(Store(directory), 'https://localhost:8765/oauth/callback')
        service.sample()
        template = service.store.all('listing')[0]
        inventory = template['snapshot']['inventory']
        for row in service.store.all('listing'):
            service.store.delete('listing', row['listing_id'])
        service.store.put('shop', 'demo', {'shop_id':'demo', 'name':'Offline shop'})
        service.store.put('listing', 'other', {'listing_id':'other','shop_id':'other'})
        service.store.put('batch', 'pending', {'id':'pending','status':'draft'})
        rows = {str(n):{**copy.deepcopy(template), 'listing_id':n, 'shop_id':'demo', 'updated_timestamp':1000000000, 'last_modified_timestamp':1000000000, 'state':'active'} for n in range(1,310)}
        for row in rows.values():
            row.pop('snapshot', None)
        inventories = {lid:copy.deepcopy(inventory) for lid in rows}
        calls, hidden = [], set()
        duplicate = [False]
        failure = [None]

        class FakeEtsy:
            def __init__(self, *args): pass
            def request(self, path):
                calls.append(path)
                if failure[0] and failure[0] in path:
                    raise EtsyError('Simulated failure', 403)
                if path == '/seller-taxonomy/nodes': return {'results':[]}
                if path.endswith('/sections'): return {'results':[]}
                if '/listings/batch/inventory?' in path:
                    query=urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                    ids=query['listing_ids'][0].split(',')
                    assert 0<len(ids)<=100
                    if failure[0]=='second-batch' and sum('/batch/inventory?' in c for c in calls)==2:
                        raise EtsyError('Simulated batch failure',500)
                    result=[{**copy.deepcopy(rows[lid]),'inventory':copy.deepcopy(inventories[lid])} for lid in ids]
                    if failure[0]=='incomplete': result.pop()
                    if failure[0]=='duplicate': result[-1]=result[0]
                    if failure[0]=='wrong-shop': result[0]['shop_id']='other'
                    if failure[0]=='null-inventory': result[0]['inventory']=None
                    return {'count':len(result),'results':result}
                if '/shops/' in path:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
                    found = [r for key,r in rows.items() if r['state']==query['state'][0] and key not in hidden]
                    if duplicate[0] and query['state'][0]=='draft':
                        found.append(copy.deepcopy(rows['2']))
                    start = int(query['offset'][0])
                    return {'count':len(found),'results':copy.deepcopy(found[start:start+100])}
                lid = urllib.parse.urlsplit(path).path.split('/')[2]
                if lid not in rows: raise EtsyError('Removed', 404)
                if '/inventory' in path: return copy.deepcopy(inventories[lid])
                return copy.deepcopy(rows[lid])

        def sync(full=False):
            calls.clear()
            service.sync_shop('demo', full=full)
            stats = service.shop('demo')['sync_stats']
            assert stats['inventory_requests'] == sum('/inventory' in c for c in calls)
            assert service.store.get('listing','other')['shop_id']=='other'
            assert service.store.get('batch','pending')['status']=='draft'
            return stats

        with patch('service.Etsy', FakeEtsy):
            assert sync()['inventory_requests']==309
            stats=sync()
            assert stats['refreshed']==0 and stats['reused']==309 and stats['summary_requests']==8 and stats['inventory_requests']==4,stats
            # All four inventory changes leave every summary/timestamp unchanged.
            summaries_before=copy.deepcopy(rows)
            inventories['1']['products'][0]['offerings'][0]['price']+=1
            inventories['2']['products'][0]['offerings'][0]['quantity']+=1
            inventories['3']['products'][0]['sku']='new-sku'
            inventories['4']['products'][0]['property_values'][0]['values']=['New option']
            assert rows==summaries_before
            stats=sync()
            assert stats['refreshed']==4 and stats['reused']==305 and stats['inventory_requests']==4,stats
            for lid in ['1','2','3','4']:
                assert service.store.get('listing',lid)['snapshot']['inventory']==inventories[lid]
            rows['2']['state']='inactive'
            rows['310']={**copy.deepcopy(rows['1']),'listing_id':310}
            inventories['310']=copy.deepcopy(inventory)
            stats=sync()
            assert stats['refreshed']==1 and stats['status_changes']==1,stats
            assert service.store.get('listing','2')['state']=='inactive'
            duplicate[0]=True
            assert sync()['refreshed']==0
            assert '/listings/2?includes=Images' in calls
            assert service.store.get('listing','2')['state']=='inactive'
            duplicate[0]=False
            # Missing page entries are reconciled directly, not blindly deleted.
            hidden.add('4')
            assert sync()['removed']==0
            del rows['5']
            assert sync()['removed']==1
            assert service.store.get('listing','5') is None
            # Neither a failed listing scan nor a partially fetched inventory is committed.
            for fail in ('state=inactive', 'second-batch', '/listings/4', 'incomplete', 'duplicate', 'wrong-shop'):
                before = service.store.all('listing'), service.shop('demo')
                inventories['6']['products'][0]['sku']+='changed'
                inventories['7']['products'][0]['sku']+='changed'
                failure[0]=fail
                try: sync()
                except ReviewError: pass
                else: raise AssertionError('Expected failure: '+fail)
                assert (service.store.all('listing'),service.shop('demo'))==before
                failure[0]=None
            assert sync()['refreshed']==2
            # Null batch inventory uses the established individual read.
            failure[0]='null-inventory'
            assert sync()['inventory_requests']==8  # four batches + one fallback each
            failure[0]=None
            # Option order and a third dimension survive batch normalization.
            products=inventories['8']['products']
            products.reverse()
            for product in products:
                product['property_values'].append({'property_id':999,'property_name':'Third','values':['Example'],'value_ids':[],'scale_id':None})
            assert sync()['refreshed']==1
            assert service.store.get('listing','8')['snapshot']['inventory']['products']==products
            rows['8'].pop('updated_timestamp');rows['8'].pop('last_modified_timestamp')
            assert sync()['refreshed']==0  # timestamps never control inventory reads
            assert sync(full=True)['inventory_requests']==309
        service.stop.set()
    print('Sync checks passed: 309 listings use four routine inventory requests; price/stock/SKU/options detected with unchanged timestamps; status, removals, batch validation, null fallback, order, third dimension, atomic failures and shop isolation verified.')


if __name__=='__main__': main()
