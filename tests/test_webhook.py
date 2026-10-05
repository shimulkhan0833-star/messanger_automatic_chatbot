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
        self.modes = {}
        self.controls = set()

    def set_mode(self, sender, mode, mid, received):
        if mid in self.controls:
            return
        self.controls.add(mid)
        self.modes[sender] = mode
        if mode == 'manual':
            for job in self.jobs.values():
                if job['sender'] == sender and job['status'] == 'pending':
                    job['status'] = 'manual'

    def can_reply(self, mid):
        job = self.jobs[mid]
        return job['status'] == 'pending' and self.modes.get(job['sender'], 'auto') == 'auto'

    def enqueue(self, mid, sender, text, received, attachments=None):
        self.jobs.setdefault(mid, dict(mid=mid, sender=sender, text=text,
            received=received, status='manual' if self.modes.get(sender) == 'manual' else 'pending',
            attempts=0, reply=None, attachments=attachments))

    def save_prepared_text(self, mid, text):
        self.jobs[mid]['prepared_text'] = text

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

    def deliver(self, event):
        return self.post({'object': 'page', 'entry': [{'id': 'page', 'messaging': [event]}]})

    def command(self, text, mid, **extra):
        event = self.event(text=text, mid=mid, is_echo=True, **extra)
        event['sender'], event['recipient'] = {'id': 'page'}, {'id': 'user'}
        return event

    def test_pause_resume_and_duplicate_command(self):
        self.deliver(self.event())
        pause = self.command(' PAUSE AI ', 'pause')
        self.assertEqual(self.deliver(pause).status_code, 200)
        self.assertEqual(self.store.jobs['m1']['status'], 'manual')
        self.deliver(self.event(mid='m2'))
        self.assertEqual(self.store.jobs['m2']['status'], 'manual')
        self.deliver(self.command('resume ai', 'resume'))
        self.deliver(pause)
        self.deliver(self.event(mid='m3'))
        self.assertEqual(self.store.jobs['m3']['status'], 'pending')
        self.assertEqual(self.store.jobs['m1']['status'], 'manual')

    def test_customer_cannot_resume_and_bot_commands_ignored(self):
        self.deliver(self.command('pause ai', 'pause'))
        self.deliver(self.event(mid='customer', text='resume ai'))
        self.deliver(self.command('resume ai', 'bot', app_id='123'))
        self.deliver(self.command('resume ai', 'metadata', metadata='bot'))
        self.assertEqual(self.store.modes['user'], 'manual')

    def test_page_inbox_app_commands_accepted(self):
        self.deliver(self.event())
        self.deliver(self.command('pause ai', 'inbox-pause', app_id=263902037430900))
        self.assertEqual(self.store.modes['user'], 'manual')
        self.assertEqual(self.store.jobs['m1']['status'], 'manual')
        self.deliver(self.command('resume ai', 'inbox-resume', app_id=263902037430900))
        self.assertEqual(self.store.modes['user'], 'auto')

    def test_attachment_only_and_caption_are_queued(self):
        for kind in ('audio', 'image'):
            self.deliver(self.event(mid=kind, text=None, attachments=[
                {'type': kind, 'payload': {'url': 'https://lookaside.fbsbx.com/media'}}]))
            self.assertEqual(self.store.jobs[kind]['attachments'][0]['type'], kind)
        self.deliver(self.event(mid='caption', text='What is this?', attachments=[
            {'type': 'image', 'payload': {'url': 'https://lookaside.fbsbx.com/photo'}}]))
        self.assertEqual(self.store.jobs['caption']['text'], 'What is this?')

    def test_unsupported_attachments_are_ignored(self):
        self.deliver(self.event(text=None, attachments=[{'type': 'video', 'payload': {'url': 'https://example.com/video'}}]))
        self.assertEqual(self.store.jobs, {})

    def test_worker_media_handoff_pause_and_normal_reply(self):
        async def check(mode):
            store = MemoryStore()
            store.enqueue('media', 'user', '', time.time(), [{'type': 'audio', 'url': 'https://lookaside.fbsbx.com/audio'}])
            if mode == 'paused':
                store.set_mode('user', 'manual', 'pause', time.time())
            ready = asyncio.Event()
            async def prepare(*args):
                ready.set()
                if mode == 'during':
                    store.set_mode('user', 'manual', 'pause', time.time())
                return 'I want to speak to a person' if mode == 'handoff' else 'Tell me about earbuds'
            with patch('techrock.services.worker.prepare_media', side_effect=prepare) as prep, \
                 patch('techrock.services.worker.generate_reply', new=AsyncMock(return_value='Here is some information')) as generate, \
                 patch('techrock.services.worker.send_reply', new=AsyncMock()) as send:
                task = asyncio.create_task(worker(store, None))
                if mode != 'paused':
                    await asyncio.wait_for(ready.wait(), 2)
                await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                if mode == 'normal':
                    send.assert_awaited_once()
                    self.assertEqual(store.history('user')[0][1], 'Tell me about earbuds')
                else:
                    send.assert_not_awaited()
                if mode == 'paused':
                    prep.assert_not_called()
                if mode == 'handoff':
                    generate.assert_not_awaited()
                    self.assertEqual(store.modes['user'], 'manual')
        for mode in ('normal', 'handoff', 'paused', 'during'):
            asyncio.run(check(mode))

    def test_media_cache_reused_after_send_failure(self):
        async def check():
            store = MemoryStore()
            store.enqueue('media', 'user', '', time.time(), [{'type': 'audio', 'url': 'https://lookaside.fbsbx.com/audio'}])
            def retry(mid, attempts):
                store.jobs[mid]['attempts'] = attempts
                store.jobs[mid]['status'] = 'pending'
            delivered = asyncio.Event()
            calls = 0
            async def send(*args):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise RuntimeError('Temporary failure')
                delivered.set()
            with patch.object(store, 'fail', side_effect=retry), \
                 patch('techrock.services.worker.prepare_media', new=AsyncMock(return_value='Question')) as prep, \
                 patch('techrock.services.worker.generate_reply', new=AsyncMock(return_value='Answer')) as generate, \
                 patch('techrock.services.worker.send_reply', side_effect=send):
                task = asyncio.create_task(worker(store, None))
                await asyncio.wait_for(delivered.wait(), 2)
                await asyncio.sleep(0.02)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                prep.assert_awaited_once()
                generate.assert_awaited_once()
                self.assertEqual(store.jobs['media']['status'], 'done')
                self.assertEqual(store.history('user')[0][1], 'Question')
        asyncio.run(check())

    def test_handoff_silently_pauses_only_requesting_customer(self):
        self.deliver(self.event(text='I want to speak to a person'))
        self.assertEqual(self.store.modes['user'], 'manual')
        self.assertEqual(self.store.jobs['m1']['status'], 'manual')
        other = self.event(mid='other')
        other['sender']['id'] = 'other-user'
        self.deliver(other)
        self.assertEqual(self.store.jobs['other']['status'], 'pending')

    def test_worker_handoff_and_pause_during_generation(self):
        async def check(handoff):
            store = MemoryStore()
            store.enqueue('m1', 'user', 'Help', time.time())
            finished = asyncio.Event()
            async def generate(*args):
                if not handoff:
                    store.set_mode('user', 'manual', 'pause', time.time())
                    store.set_mode('user', 'auto', 'resume', time.time())
                finished.set()
                return None if handoff else 'Answer'
            with patch('techrock.services.worker.generate_reply', side_effect=generate), \
                 patch('techrock.services.worker.send_reply', new=AsyncMock()) as send:
                task = asyncio.create_task(worker(store, None))
                await asyncio.wait_for(finished.wait(), 2)
                await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                send.assert_not_awaited()
                self.assertEqual(store.jobs['m1']['status'], 'manual')
        asyncio.run(check(True))
        asyncio.run(check(False))

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
