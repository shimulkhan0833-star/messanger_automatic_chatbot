import hashlib
import hmac
import json
import os
import time

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from main import create_app
from techrock.services.worker import worker
import asyncio
from unittest.mock import AsyncMock


class MemoryStore:
    """Test double: webhook tests do not require a running MySQL server."""
    def __init__(self):
        self.jobs = {}
        self.messages = {}

    def enqueue(self, mid, sender, text, received):
        self.jobs.setdefault(mid, dict(mid=mid, sender=sender, text=text,
            received=received, status='pending', attempts=0, reply=None))

    def next_job(self):
        return next((j for j in self.jobs.values() if j['status']=='pending'), None)

    def history(self, sender):
        return self.messages.get(sender, [])

    def save_reply(self, mid, reply):
        self.jobs[mid]['reply'] = reply

    def complete(self, job, reply):
        job['status'] = 'done'
        self.messages.setdefault(job['sender'], []).extend([('human', job['text']), ('ai', reply)])

    def expire(self, mid):
        self.jobs[mid]['status'] = 'expired'

    def fail(self, mid, attempts):
        self.jobs[mid]['attempts'] = attempts
        self.jobs[mid]['status'] = 'failed'

    def close(self):
        pass


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'META_VERIFY_TOKEN': 'test-token',
            'META_APP_SECRET': 'test-secret', 'META_PAGE_ID': 'page'})
        self.env.start()
        self.store = MemoryStore()
        self.client = TestClient(create_app(store_factory=lambda: self.store, run_worker=False))
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()

    def post(self, payload):
        raw = json.dumps(payload).encode()
        sig = 'sha256=' + hmac.new(b'test-secret', raw, hashlib.sha256).hexdigest()
        return self.client.post('/webhook', content=raw,
            headers={'x-hub-signature-256': sig, 'content-type': 'application/json'})

    def event(self, **message):
        return {'sender': {'id': 'user'}, 'recipient': {'id': 'page'},
                'timestamp': int(time.time() * 1000), 'message': {'mid': 'm1', 'text': 'Hello', **message}}

    def test_verification(self):
        params = {'hub.mode': 'subscribe', 'hub.verify_token': 'test-token', 'hub.challenge': '123'}
        response = self.client.get('/webhook', params=params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, '123')
        params['hub.verify_token'] = 'wrong'
        self.assertEqual(self.client.get('/webhook', params=params).status_code, 403)

    def test_unsigned_request_rejected(self):
        self.assertEqual(self.client.post('/webhook', json={'object': 'page'}).status_code, 403)

    def test_queue_deduplicates(self):
        payload = {'object': 'page', 'entry': [{'id': 'page', 'messaging': [self.event()]}]}
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(self.post(payload).status_code, 200)
        self.assertEqual(len(self.store.jobs), 1)

    def test_echo_and_other_page_ignored(self):
        self.post({'object': 'page', 'entry': [{'id': 'page', 'messaging': [self.event(is_echo=True)]},
            {'id': 'other', 'messaging': [self.event()]}]})
        self.assertEqual(len(self.store.jobs), 0)

    def test_malformed_signed_payload(self):
        self.assertEqual(self.post({'object': 'page', 'entry': 'bad'}).status_code, 400)

    def test_worker_reply_and_memory(self):
        async def check():
            store = MemoryStore()
            store.enqueue('m1', 'user', 'Hello', time.time())
            with patch('techrock.services.worker.generate_reply', new=AsyncMock(return_value='Hi!')) as generate, \
                 patch('techrock.services.worker.send_reply', new=AsyncMock()) as send:
                task = asyncio.create_task(worker(store, None))
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                generate.assert_awaited_once_with('Hello', [])
                send.assert_awaited_once_with(None, 'user', 'Hi!')
                self.assertEqual(store.history('user'), [('human', 'Hello'), ('ai', 'Hi!')])
            store.close()
        asyncio.run(check())


if __name__ == '__main__':
    unittest.main()
