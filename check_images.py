"""Focused image-flow checks. A fake Etsy transport; never contacts a shop."""
import base64
import copy
import tempfile
from pathlib import Path
from engine import ReviewError
from image_edits import image_ids, make_plan
from service import Service
from storage import Store

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


class FakeEtsy:
    def __init__(self, listing):
        self.listing = listing
        self.images = [{'listing_image_id': i, 'rank': i, 'url_170x135': '/sample-image.svg'} for i in range(1, 6)]
        self.links = []
        self.uploads = 0
        self.lose_upload_response = False

    def request(self, path, method='GET', data=None, form=False, content_type=None):
        if method == 'GET':
            if path.endswith('/variation-images'):
                return {'results': self.links}
            if path.endswith('/images'):
                return {'results': copy.deepcopy(self.images)}
            return self.listing
        if method == 'DELETE':
            key = path.rsplit('/', 1)[1]
            self.images = [i for i in self.images if str(i['listing_image_id']) != key]
            self.rank()
            return {}
        if method == 'POST':
            assert content_type.startswith('multipart/form-data; boundary=')
            assert PNG in data and b'name="image"; filename="image.png"' in data
            self.uploads += 1
            item = {'listing_image_id': 100 + self.uploads, 'rank': len(self.images) + 1, 'url_170x135': '/sample-image.svg'}
            self.images.append(item)
            if self.lose_upload_response:
                raise ReviewError('Response lost after upload')
            return item
        if method == 'PATCH':
            lookup = {str(i['listing_image_id']): i for i in self.images}
            self.images = [lookup[str(k)] for k in data['image_ids']]
            self.rank()
            return self.listing
        raise AssertionError((method, path))

    def rank(self):
        for n, image in enumerate(self.images, 1):
            image['rank'] = n


def main():
    with tempfile.TemporaryDirectory() as directory:
        service = Service(Store(directory), 'https://localhost:8765/oauth/callback')
        service.sample()
        shop = service.shop('sample')
        shop['sample'] = False
        service.store.put('shop', 'sample', shop)
        listing = service.store.all('listing')[0]
        lid = str(listing['listing_id'])
        api = FakeEtsy(listing)
        service.images.api = lambda _: api
        asset = service.images.upload('sample', 'test.png', PNG)
        service.images.gallery('sample', [lid])
        batch = service.images.review({'shop_id': 'sample', 'edits': [{'listing_id': lid, 'removed': ['1', '2'], 'uploads': [asset['id']], 'position': 'first'}]})
        assert batch['items'][0]['status'] == 'ready'
        service.images.apply(batch)
        assert batch['status'] == 'complete'
        assert image_ids(api.images) == ['101', '3', '4', '5']
        service.images.apply(batch)
        assert api.uploads == 1, 'Repeat application uploaded twice'
        # Gallery conflicts must stop removals and additions.
        service.images.gallery('sample', [lid])
        conflict = service.images.review({'shop_id': 'sample', 'edits': [{'listing_id': lid, 'removed': ['5'], 'uploads': [], 'position': 'last'}]})
        api.images[0], api.images[1] = api.images[1], api.images[0]
        api.rank()
        service.images.apply(conflict)
        assert conflict['items'][0]['status'] == 'conflict'
        assert '5' in image_ids(api.images)
        # A response lost after upload must never cause a duplicate retry.
        service.images.gallery('sample', [lid])
        uncertain = service.images.review({'shop_id': 'sample', 'edits': [{'listing_id': lid, 'removed': [], 'uploads': [asset['id']], 'position': 'last'}]})
        api.lose_upload_response = True
        service.images.apply(uncertain)
        attempts = api.uploads
        service.images.apply(uncertain)
        assert api.uploads == attempts
        assert uncertain['items'][0]['status'] == 'uncertain'
        # Variation-linked images and removing all without replacement are blocked.
        api.links = [{'listing_image_id': 5}]
        service.images.gallery('sample', [lid])
        blocked = service.images.review({'shop_id': 'sample', 'edits': [{'listing_id': lid, 'removed': ['5'], 'uploads': [], 'position': 'last'}]})
        assert blocked['items'][0]['status'] == 'attention'
        try:
            make_plan(api.images, image_ids(api.images), [], 'last', 'active')
            raise AssertionError('Allowed an empty active gallery')
        except ReviewError:
            pass
        # Replace every image without ever emptying the live gallery.
        api.lose_upload_response = False
        api.links = []
        service.images.gallery('sample', [lid])
        replacement = service.images.review({'shop_id': 'sample', 'edits': [{'listing_id': lid, 'removed': image_ids(api.images), 'uploads': [asset['id']], 'position': 'last'}]})
        service.images.apply(replacement)
        assert replacement['status'] == 'complete' and len(api.images) == 1
        print('Image checks passed: ordering, full replacement, conflict detection, linked-image protection, repeated save, and uncertain upload recovery.')


if __name__ == '__main__':
    main()
