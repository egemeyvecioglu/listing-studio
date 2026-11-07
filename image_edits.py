"""Local gallery editing and durable, explicitly approved Etsy image updates."""
import hashlib
import secrets
import time
import uuid
from pathlib import Path

from engine import ReviewError, fingerprint

MAX_IMAGES = 20
MAX_UPLOAD = 20 * 1024 * 1024


def ordered(images):
    return sorted(images, key=lambda x: (int(x.get('rank') or 0), int(x['listing_image_id'])))


def image_ids(images):
    return [str(i['listing_image_id']) for i in ordered(images)]


def make_plan(images, removed, uploads, position, state):
    before = image_ids(images)
    if not isinstance(removed, list) or len(removed) != len(set(map(str, removed))):
        raise ReviewError('The image selection is invalid. Refresh the gallery.')
    removed = set(map(str, removed))
    if not removed.issubset(before):
        raise ReviewError('An image changed or disappeared. Refresh the gallery and review again.')
    if position not in ('first', 'last'):
        raise ReviewError('Choose beginning or end for new images.')
    keep = [i for i in before if i not in removed]
    added = ['upload:' + u for u in uploads]
    after = added + keep if position == 'first' else keep + added
    if len(after) > MAX_IMAGES:
        raise ReviewError('Etsy allows up to 20 images. Remove more images or choose fewer uploads.')
    if not after and (before or state == 'active'):
        raise ReviewError('Keep at least one image, or add a replacement before removing all images.')
    return {'before_ids': before, 'remove_ids': [i for i in before if i in removed], 'desired': after, 'uploads': uploads, 'position': position}


class ImageEdits:
    def __init__(self, service):
        self.service = service
        self.store = service.store
        self.directory = self.store.directory / 'image-uploads'
        self.directory.mkdir(exist_ok=True)

    def upload(self, shop_id, name, raw):
        self.service.shop(shop_id)
        if not raw or len(raw) > MAX_UPLOAD:
            raise ReviewError('Choose a JPG or PNG image smaller than 20 MB.')
        if raw.startswith(b'\x89PNG\r\n\x1a\n'):
            content_type, extension = 'image/png', '.png'
        elif raw.startswith(b'\xff\xd8\xff'):
            content_type, extension = 'image/jpeg', '.jpg'
        else:
            raise ReviewError('Choose a JPG or PNG file. Other formats are not supported.')
        key = str(uuid.uuid4())
        path = self.directory / (key + extension)
        path.write_bytes(raw)
        path.chmod(0o600)
        asset = {'id': key, 'shop_id': str(shop_id), 'name': Path(name.replace('\\', '/')).name[:150] or ('image' + extension), 'content_type': content_type, 'extension': extension, 'sha256': hashlib.sha256(raw).hexdigest(), 'created_at': time.time()}
        self.store.put('image_upload', key, asset)
        return self.public_asset(asset)

    def public_asset(self, asset):
        return {'id': asset['id'], 'name': asset['name'], 'url': '/api/image-file?id=' + asset['id']}

    def asset(self, key, shop_id=None):
        asset = self.store.get('image_upload', key)
        if not asset or (shop_id is not None and asset['shop_id'] != str(shop_id)):
            raise ReviewError('This local image is missing or belongs to another shop. Choose the file again.')
        return asset

    def file(self, key):
        asset = self.asset(key)
        path = self.directory / (asset['id'] + asset['extension'])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != asset['sha256']:
            raise ReviewError('A local image changed. Choose the file again.')
        return raw, asset['content_type']

    def api(self, shop_id):
        from service import Etsy
        return Etsy(self.service, shop_id)

    def current(self, api, listing_id):
        return ordered(api.request('/listings/' + str(listing_id) + '/images').get('results', []))

    def gallery(self, shop_id, ids):
        shop = self.service.shop(shop_id)
        if not isinstance(ids, list) or not 1 <= len(ids) <= 12:
            raise ReviewError('Load between one and twelve products at a time.')
        api = None if shop.get('sample') else self.api(shop_id)
        result = []
        for lid in ids:
            listing = self.store.get('listing', str(lid))
            if not listing or str(listing['shop_id']) != str(shop_id):
                raise ReviewError('This product does not belong to the selected shop.')
            if api:
                # Verify current ownership rather than relying on public image IDs.
                current = api.request('/listings/' + str(lid))
                if str(current.get('shop_id')) != str(shop_id):
                    raise ReviewError('This product belongs to a different shop.')
                images = self.current(api, lid)
                links = api.request('/shops/' + str(shop_id) + '/listings/' + str(lid) + '/variation-images').get('results', [])
            else:
                images, links = listing.get('images', []), []
                if not images:
                    images = [{'listing_image_id': int(lid) * 100 + i, 'rank': i, 'url_170x135': '/sample-image.svg', 'url_fullxfull': '/sample-image.svg'} for i in range(1, 6)]
            gallery = {'listing_id': str(lid), 'shop_id': str(shop_id), 'title': listing['title'], 'state': listing['state'], 'images': ordered(images), 'linked_ids': [str(v['listing_image_id']) for v in links if v.get('listing_image_id')], 'checked_at': time.time()}
            self.store.put('gallery', str(lid), gallery)
            listing['images'] = images
            self.store.put('listing', str(lid), listing)
            result.append(gallery)
        return result

    def review(self, data):
        shop_id = str(data.get('shop_id', ''))
        shop = self.service.shop(shop_id)
        edits = data.get('edits')
        if not isinstance(edits, list) or not edits:
            raise ReviewError('Add or remove an image first.')
        if len(edits) > 1000:
            raise ReviewError('Choose fewer products in this batch.')
        batch = {'id': str(uuid.uuid4()), 'type': 'images', 'shop_id': shop_id, 'shop_name': shop['name'], 'sample': bool(shop.get('sample')), 'label': 'Image updates', 'created_at': time.time(), 'status': 'review', 'rules': [], 'filters': {}, 'items': []}
        seen = set()
        for edit in edits:
            lid = str(edit.get('listing_id'))
            if lid in seen:
                raise ReviewError('Each product can appear only once in an image review.')
            seen.add(lid)
            gallery = self.store.get('gallery', lid)
            if not gallery or gallery['shop_id'] != shop_id:
                raise ReviewError('Load this shop’s galleries before preparing a review.')
            item = {'listing_id': lid, 'title': gallery['title'], 'changes': [], 'before_images': gallery['images'], 'mapped': {}, 'expected_ids': image_ids(gallery['images']), 'journal': [], 'error': ''}
            try:
                uploads = edit.get('uploads', [])
                if not isinstance(uploads, list) or len(set(uploads)) != len(uploads):
                    raise ReviewError('An image was added twice. Remove the duplicate before reviewing.')
                assets = [self.asset(k, shop_id) for k in uploads]
                plan = make_plan(gallery['images'], edit.get('removed', []), uploads, edit.get('position', 'last'), gallery['state'])
                if set(plan['remove_ids']) & set(gallery['linked_ids']):
                    raise ReviewError('A removed image is linked to a variation. Keep it, or unlink it on Etsy and refresh this gallery first.')
                lookup = {str(i['listing_image_id']): {'key': str(i['listing_image_id']), 'url': i.get('url_170x135') or i.get('url_fullxfull'), 'name': 'Image ' + str(n)} for n, i in enumerate(gallery['images'], 1)}
                lookup.update({'upload:' + a['id']: {'key': 'upload:' + a['id'], **self.public_asset(a)} for a in assets})
                item.update(plan=plan, lookup=lookup, after_images=[lookup[k] for k in plan['desired']], status='ready' if plan['remove_ids'] or uploads else 'unchanged')
                item['changes'] = [{'label': 'Images', 'before': len(gallery['images']), 'after': len(plan['desired'])}]
            except ReviewError as exc:
                item.update(status='attention', error=str(exc), after_images=[])
            batch['items'].append(item)
        self.store.put('batch', batch['id'], batch)
        self.service.touch()
        return batch

    def multipart(self, fields, raw=None, content_type=None):
        boundary = 'ListingStudio' + secrets.token_hex(16)
        chunks = []
        for key, value in fields.items():
            chunks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="' + key + '"\r\n\r\n' + str(value) + '\r\n').encode())
        if raw is not None:
            chunks.append(('--' + boundary + '\r\nContent-Disposition: form-data; name="image"; filename="image.' + ('png' if content_type == 'image/png' else 'jpg') + '"\r\nContent-Type: ' + content_type + '\r\n\r\n').encode() + raw + b'\r\n')
        chunks.append(('--' + boundary + '--\r\n').encode())
        return b''.join(chunks), 'multipart/form-data; boundary=' + boundary

    def apply(self, batch):
        batch['status'] = 'applying'
        self.store.put('batch', batch['id'], batch)
        api = None if batch['sample'] else self.api(batch['shop_id'])
        for item in batch['items']:
            if item['status'] in ('complete', 'unchanged', 'attention', 'conflict'):
                continue
            try:
                self.apply_item(batch, item, api)
            except ReviewError as exc:
                item['status'] = 'uncertain' if item.get('sending') else 'failed'
                item['error'] = str(exc)
            except Exception:
                item.update(status='uncertain', error='The image update could not be verified. Refresh the gallery before preparing another review.')
            self.store.put('batch', batch['id'], batch)
        batch['status'] = 'complete' if all(i['status'] in ('complete', 'unchanged') for i in batch['items']) else 'needs_attention'
        self.store.put('batch', batch['id'], batch)

    def apply_item(self, batch, item, api):
        lid = item['listing_id']
        base = '/shops/' + batch['shop_id'] + '/listings/' + lid
        def save():
            self.store.put('batch', batch['id'], batch)
        def fetch():
            if api:
                return self.current(api, lid)
            return self.store.get('listing', lid).get('images', item['before_images'])
        def set_sample(images):
            listing = self.store.get('listing', lid)
            listing['images'] = images
            self.store.put('listing', lid, listing)
        if api:
            listing = api.request('/listings/' + lid)
            if str(listing.get('shop_id')) != batch['shop_id']:
                raise ReviewError('This product belongs to another shop.')
        images = fetch()
        current = image_ids(images)
        desired = lambda: [str(item['mapped'].get(k[7:], k)) if k.startswith('upload:') else k for k in item['plan']['desired']]
        sending = item.get('sending')
        if sending:
            # Reconcile known removals/reorders. An upload with a lost response
            # cannot safely be retried: Etsy has no upload idempotency key.
            if sending['kind'] == 'upload':
                raise ReviewError('An upload result is uncertain. Do not retry it automatically. Refresh this product’s gallery and inspect it before preparing a new review.')
            expected_after = sending['after']
            if current == expected_after:
                item['expected_ids'] = current
                item.pop('sending', None)
                save()
            elif current == item['expected_ids']:
                item.pop('sending', None)
            else:
                item.update(status='conflict', error='The gallery changed on Etsy. Refresh it and prepare a new review.')
                return
        if current != item['expected_ids']:
            item.update(status='conflict', error='The gallery changed since the preview. Refresh it and prepare a new review.')
            return
        if api and item['plan']['remove_ids']:
            links = api.request(base + '/variation-images').get('results', [])
            if {str(v.get('listing_image_id')) for v in links} & set(item['plan']['remove_ids']):
                item.update(status='conflict', error='An image is now linked to a variation. Keep it or unlink it on Etsy before trying again.')
                return

        def check():
            nonlocal images, current
            images = fetch()
            current = image_ids(images)
            if current != item['expected_ids']:
                raise ReviewError('The gallery changed during this update. Refresh it before continuing.')

        def remove(key):
            nonlocal images, current
            check()
            after = [i for i in current if i != key]
            item['sending'] = {'kind': 'remove', 'after': after}
            save()
            if api:
                api.request(base + '/images/' + key, 'DELETE')
            else:
                set_sample([i for i in images if str(i['listing_image_id']) != key])
            images = fetch()
            if image_ids(images) != after:
                raise ReviewError('Etsy did not confirm the image removal. Refresh the gallery before continuing.')
            current = after
            item['expected_ids'] = after
            item['journal'].append({'removed': key})
            item.pop('sending', None)
            save()

        # Preserve at least one image until a replacement has been uploaded.
        deferred = []
        for key in item['plan']['remove_ids']:
            if key not in current:
                continue
            if len(current) == 1:
                deferred.append(key)
            else:
                remove(key)
        for key in item['plan']['uploads']:
            if key in item['mapped']:
                continue
            check()
            if len(current) >= MAX_IMAGES:
                raise ReviewError('No free image slot is available. Refresh the gallery and review the remaining images.')
            asset = self.asset(key, batch['shop_id'])
            raw, content_type = self.file(key)
            item['sending'] = {'kind': 'upload', 'asset_id': key}
            save()
            append_rank = max((int(i.get('rank') or 0) for i in images), default=0) + 1
            if api:
                body, multipart_type = self.multipart({'rank': append_rank, 'overwrite': 'false'}, raw, content_type)
                response = api.request(base + '/images', 'POST', body, content_type=multipart_type)
                new_id = str(response['listing_image_id'])
            else:
                new_id = str(int(uuid.uuid4().hex[:13], 16))
                set_sample(images + [{'listing_image_id': new_id, 'rank': append_rank, 'url_170x135': '/api/image-file?id=' + key}])
            # Persist the returned ID before the next network request.
            item['mapped'][key] = new_id
            item['expected_ids'] = current + [new_id]
            item['journal'].append({'uploaded': new_id, 'asset_id': key})
            item.pop('sending', None)
            save()
            check()
            for old in deferred[:]:
                remove(old)
                deferred.remove(old)
        target = desired()
        check()
        if current != target:
            item['sending'] = {'kind': 'reorder', 'after': target}
            save()
            if api:
                api.request(base, 'PATCH', {'image_ids': target}, form=True)
            else:
                lookup = {str(i['listing_image_id']): i for i in images}
                set_sample([{**lookup[k], 'rank': n} for n, k in enumerate(target, 1)])
            images = fetch()
            if image_ids(images) != target:
                raise ReviewError('The final gallery order could not be verified. Refresh this product and inspect its cover image.')
            item['expected_ids'] = target
            item.pop('sending', None)
            save()
        item.update(status='complete', error='', verified_images=images)
        listing = self.store.get('listing', lid)
        listing['images'] = images
        self.store.put('listing', lid, listing)
        self.store.delete('gallery', lid)
