"""Regression for additive previews and explicit dependency approval; no Etsy calls."""
import copy
import tempfile
from engine import ReviewError, changed_dependencies, fingerprint, product_key
from service import Service
from storage import Store


def fixture(service):
    service.sample()
    listings = service.store.all('listing')[:2]
    for row in listings:
        template = copy.deepcopy(row['snapshot']['inventory']['products'][0])
        template['property_values'][0]['values'] = ['Adult-Unisex Hoodie 2XL']
        template['sku'] = 'SHARED-SKU'
        template['offerings'][0].update(price=34, quantity=20)
        inventory = row['snapshot']['inventory']
        inventory.update(products=[], price_on_property=[], quantity_on_property=[], sku_on_property=[])
        for n in range(29):
            product = copy.deepcopy(template)
            product['property_values'][1]['values'] = ['Color ' + str(n + 1)]
            inventory['products'].append(product)
        service.store.put('listing', row['listing_id'], row)
    rules = [{'kind':'option','mode':'copy','auto_source':True,'property_id':'513','old':'Adult-Unisex Hoodie 2XL','value':'Youth Long Sleeve '+size,'price':25,'quantity':10,'sku':'','filters':{}} for size in ['S','M','L']]
    return listings, rules


def main():
    with tempfile.TemporaryDirectory() as directory:
        service = Service(Store(directory), 'https://localhost:8765/oauth/callback')
        rows, rules = fixture(service)
        batch = service.build_review({'shop_id':'sample','listing_ids':[r['listing_id'] for r in rows],'rules':rules})
        for item in batch['items']:
            assert item['status'] == 'ready', item.get('error')
            old = {product_key(p):p for p in item['before']['inventory']['products']}
            new = {product_key(p):p for p in item['after']['inventory']['products']}
            assert all(new[k] == p for k,p in old.items())
            assert len(new) - len(old) == 87
            added = [c for c in item['changes'] if c.get('kind') == 'added']
            assert len(added) == 87 and all(c['before'] is None for c in added)
            for size in ['S','M','L']:
                group = [c for c in added if c['option'] == 'Youth Long Sleeve '+size]
                assert len(group) == 29 and len({v for c in group for v in c['colors']}) == 29
            inventory = item['after']['inventory']
            assert inventory['sku_on_property'] == [], 'Blank optional SKU must inherit, not force a setting change'
            assert inventory['quantity_on_property'] == [513], 'Stock only needs to vary by style/size'
            assert inventory['price_on_property'] == [513]
            assert len([c for c in item['changes'] if c.get('kind') == 'dependency']) == 2
        before_save = [fingerprint(service.store.get('listing', r['listing_id'])['snapshot']) for r in rows]
        try:
            service.apply_batch(batch['id'])
            raise AssertionError('Dependency settings changed without separate approval')
        except ReviewError:
            pass
        assert before_save == [fingerprint(service.store.get('listing', r['listing_id'])['snapshot']) for r in rows]
        approved = [i['listing_id'] for i in batch['items']]
        service.apply_batch(batch['id'], approved)
        assert service.batch(batch['id'])['status'] == 'complete'
        restore = service.restore_review(batch['id'])
        assert all(c['kind'] == 'removed' for i in restore['items'] for c in i['changes'] if c.get('option'))
        # Matching values preserve every original dependency; no extra choice.
        same = [{**r,'price':34,'quantity':20} for r in rules]
        fresh, _ = fixture(service)
        kept = service.build_review({'shop_id':'sample','listing_ids':[fresh[0]['listing_id']],'rules':same})
        assert not changed_dependencies(kept['items'][0]['before'], kept['items'][0]['after'])
        service.check_dependency_approval(kept, [])
        # Consent also protects older saved previews without new change metadata.
        old_batch = copy.deepcopy(batch)
        for item in old_batch['items']:
            item['status'] = 'ready'
            item['changes'] = []
        try:
            service.check_dependency_approval(old_batch, [])
            raise AssertionError('An older review bypassed separate approval')
        except ReviewError:
            pass
    print('Review check passed: 87 additions per listing, originals preserved, 29 colors per size, minimal dependencies, inherited SKU and separate consent.')


if __name__ == '__main__':
    main()
