"""One focused offline check for the direct-edit paths. Never contacts Etsy."""
import copy
import tempfile
from engine import ReviewError, fingerprint, preview
from service import Service
from storage import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        service = Service(Store(directory), 'https://localhost:8765/oauth/callback')
        service.sample()
        rows = service.store.all('listing')
        before = copy.deepcopy(rows[0]['snapshot'])
        additions = [{'kind': 'option', 'mode': 'copy', 'property_id': '513', 'old': 'T-shirt / M', 'value': name, 'price': 39, 'quantity': 10, 'filters': {'514': ['Ivory']}} for name in ['Hoodie / M', 'Hoodie / L']]
        after, changes = preview(before, additions)
        assert len(after['inventory']['products']) == len(before['inventory']['products']) + 2
        assert after['inventory']['products'] != before['inventory']['products']
        assert all(c['after']['Price'] == 39 for c in changes)
        assert before == rows[0]['snapshot']
        independent = [{'kind': 'price', 'mode': 'set', 'value': 25, 'filters': {'513': ['T-shirt / M']}}, {'kind': 'quantity', 'mode': 'set', 'value': 7, 'filters': {'513': ['Sweatshirt / M']}}]
        updated, _ = preview(before, independent)
        for p in updated['inventory']['products']:
            style = p['property_values'][0]['values'][0]
            assert p['offerings'][0]['price'] == (25 if style == 'T-shirt / M' else 34 if style.startswith('Sweatshirt') else 22)
            assert p['offerings'][0]['quantity'] == (7 if style == 'Sweatshirt / M' else 20)
        try:
            preview(before, [independent[0], independent[0]])
            raise AssertionError('Conflicting edits accepted')
        except ReviewError:
            pass
        rules = [{**independent[0], 'listing_ids': [str(rows[0]['listing_id'])]}]
        batch = service.build_review({'shop_id': 'sample', 'listing_ids': [r['listing_id'] for r in rows[:2]], 'rules': rules})
        assert [i['status'] for i in batch['items']] == ['ready', 'unchanged']
        service.apply_batch(batch['id'])
        service.apply_batch(batch['id'])
        restored = service.restore_review(batch['id'])
        service.apply_batch(restored['id'])
        assert fingerprint(service.store.get('listing', rows[0]['listing_id'])['snapshot']) == fingerprint(before)
        # Batch additions use each listing's own option combinations, including
        # a source missing in some colors and an already-existing target.
        batch_rows = rows[:3]
        for n, row in enumerate(batch_rows):
            row['snapshot'] = copy.deepcopy(before)
            items = row['snapshot']['inventory']['products']
            if n == 0:
                # The preferred source is absent for one color.
                items[:] = [p for p in items if not (p['property_values'][0]['values'] == ['T-shirt / M'] and p['property_values'][1]['values'] == ['Ivory'])]
            if n == 1:
                for product in items:
                    product['property_values'][0]['values'] = [product['property_values'][0]['values'][0].replace('T-shirt', 'Different tee')]
                    product['property_values'][1]['values'] = ['Unique ' + product['property_values'][1]['values'][0]]
            if n == 2:
                existing = copy.deepcopy(items[0])
                existing['property_values'][0]['values'] = ['Hoodie / M']
                items.append(existing)
            service.store.put('listing', row['listing_id'], row)
        bulk_rules = [{'kind':'option', 'mode':'copy', 'auto_source':True, 'property_id':'513', 'old':'T-shirt / M', 'value':name, 'price':39, 'quantity':10, 'filters':{}} for name in ['Hoodie / M', 'Hoodie / L']]
        bulk = service.build_review({'shop_id':'sample', 'listing_ids':[r['listing_id'] for r in batch_rows], 'rules':bulk_rules})
        for item in bulk['items']:
            assert item['status'] == 'ready', item.get('error')
            original = item['before']['inventory']['products']
            result = item['after']['inventory']['products']
            colors = {p['property_values'][1]['values'][0] for p in original}
            assert all(p in result for p in original), 'Existing options were changed'
            for name in ['Hoodie / M', 'Hoodie / L']:
                added = [p for p in result if p['property_values'][0]['values'] == [name]]
                assert len(added) == len(colors)
                assert {p['property_values'][1]['values'][0] for p in added} == colors
            _, repeated_changes = preview(item['after'], service.resolve_rules({'snapshot':item['after']}, bulk_rules, service.shop('sample')))
            assert not repeated_changes, 'Existing new options should not be duplicated'
        print('Direct-edit safety check passed: additions, prices/stock targeting, conflict checks, product scope, repeat save, restore, and batch additions across different style/color combinations.')


if __name__ == '__main__':
    main()
