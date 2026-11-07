"""Local HTTP regression: preview during sync, visible task and no queued writes."""
import json
import tempfile
import threading
import time
import urllib.error
import urllib.request
from app import server_for
from service import Service
from storage import Store


def main():
    with tempfile.TemporaryDirectory() as directory:
        service = Service(Store(directory), 'https://localhost:8765/oauth/callback')
        service.sample()
        # Let the OS choose a free port, then update the handler's expected host
        # by creating the server with that same assigned port.
        import socket
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        server = server_for(service, port)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        release = threading.Event()
        try:
            root = 'http://localhost:' + str(port)
            with urllib.request.urlopen(root) as response:
                cookie = response.headers['Set-Cookie'].split(';')[0]
            def request(path, body=None):
                headers = {'Cookie':cookie}
                if body is not None:
                    headers.update({'Origin':root,'Content-Type':'application/json','X-CSRF-Token':state['csrf']})
                req = urllib.request.Request(root+path, data=None if body is None else json.dumps(body).encode(), headers=headers)
                try:
                    with urllib.request.urlopen(req) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code, json.load(error)
            _, state = request('/api/state')
            service.background('Syncing sample shop for task test…', lambda: release.wait(10))
            _, status = request('/api/status')
            assert status['busy'] and status['message'].startswith('Syncing sample shop')
            lid = str(service.store.all('listing')[0]['listing_id'])
            code, review = request('/api/preview', {'shop_id':'sample','listing_ids':[lid],'rules':[{'kind':'price','mode':'set','value':28,'filters':{'513':['T-shirt / M']}}]})
            assert code == 200 and review['batch']['items'][0]['status'] == 'ready'
            approval = {'id':review['batch']['id'],'approval':review['approval']}
            code, busy = request('/api/apply', approval)
            assert code == 409 and busy['code'] == 'TASK_BUSY'
            assert busy['task'] == status['message']
            assert service.batch(approval['id'])['status'] == 'review'
            release.set()
            deadline = time.monotonic()+3
            while service.status['busy'] and time.monotonic()<deadline:
                time.sleep(.01)
            assert not service.status['busy']
            assert service.batch(approval['id'])['status'] == 'review', 'Save was unexpectedly queued'
            code, _ = request('/api/apply', approval)
            assert code == 200
            deadline = time.monotonic()+3
            while service.status['busy'] and time.monotonic()<deadline:
                time.sleep(.01)
            assert service.batch(approval['id'])['status'] == 'complete'
            service.begin_task('Loading listing images from Etsy…')
            try:
                _, status = request('/api/status')
                assert status['busy'] and status['message'] == 'Loading listing images from Etsy…'
            finally:
                service.end_task()
            service.background('Sample failure…', lambda: (_ for _ in ()).throw(ValueError('Sample failure')))
            deadline = time.monotonic()+3
            while service.status['busy'] and time.monotonic()<deadline:
                time.sleep(.01)
            assert not service.status['busy'] and not service.operation.locked()
            print('Task check passed: preview during sync, named busy response, review preserved, no queued write, retry and task cleanup.')
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    main()
