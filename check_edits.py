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
        print('Direct-edit safety check passed: additions, prices/stock targeting, conflict checks, product scope, repeat save and restore.')


if __name__ == '__main__':
    main()
