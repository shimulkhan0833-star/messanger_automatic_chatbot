"""FastAPI routes and application lifecycle for The Tech Rock."""
import asyncio
import hashlib
import hmac
import json
import os
import time
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

import techrock.config
from techrock.db.storage import Store
from techrock.services.worker import worker
from techrock.services.handoff import requests_person
from techrock.services.media import extract_attachments


# Build the FastAPI application, register its routes, and configure startup/shutdown handling.
# A store factory and worker flag let tests run without external services.
def create_app(store_factory=None, run_worker=True):
    # At startup, open the database and HTTP client and start the background worker.
    # At shutdown, stop the worker and close the client and database.
    @asynccontextmanager
    async def lifespan(app):
        store = await asyncio.to_thread(store_factory or Store)
        app.state.store = store
        app.state.conversation_lock = asyncio.Lock()
        async with httpx.AsyncClient(timeout=20) as client:
            task = asyncio.create_task(worker(store, client, app.state.conversation_lock)) if run_worker else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                await asyncio.to_thread(store.close)

    app = FastAPI(title='The Tech Rock Messenger Bot', lifespan=lifespan)

    # Report which required environment variables are missing, without exposing their values.
    # This checks configuration presence; it does not validate credentials with Meta or Groq.
    @app.get('/health')
    async def health():
        required = ['GROQ_API_KEY', 'META_VERIFY_TOKEN', 'META_APP_SECRET',
                    'META_PAGE_ACCESS_TOKEN', 'META_PAGE_ID']
        missing = [key for key in required if not os.getenv(key)]
        return {'status': 'needs_configuration' if missing else 'ok', 'missing': missing}

    # During Meta's webhook setup, check the verification token and return its challenge text.
    @app.get('/webhook', response_class=PlainTextResponse)
    async def verify(request: Request):
        expected = os.getenv('META_VERIFY_TOKEN', '')
        query = request.query_params
        if not expected:
            raise HTTPException(503, 'Verification token is not configured')
        if (query.get('hub.mode') == 'subscribe' and
            hmac.compare_digest(query.get('hub.verify_token', ''), expected) and
            query.get('hub.challenge')):
            return query['hub.challenge']
        raise HTTPException(403, 'Verification failed')

    # Authenticate incoming events with the App Secret and accept text messages for this Page.
    # Ignore echoes and unsupported events, save accepted messages, then acknowledge Meta quickly.
    @app.post('/webhook', response_class=PlainTextResponse)
    async def receive(request: Request):
        secret = os.getenv('META_APP_SECRET', '')
        page = os.getenv('META_PAGE_ID', '')
        # This Page inbox app ID was observed on signed manual command echoes.
        manual_apps = {value.strip() for value in os.getenv(
            'META_MANUAL_REPLY_APP_IDS', '263902037430900').split(',') if value.strip()}
        if not secret or not page:
            raise HTTPException(503, 'Meta app secret and Page ID are required')
        raw = await request.body()
        signature = 'sha256=' + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(request.headers.get('x-hub-signature-256', ''), signature):
            raise HTTPException(403, 'Invalid signature')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError()
            if payload.get('object') != 'page':
                return 'EVENT_RECEIVED'
            events = []
            for entry in payload.get('entry', []):
                if str(entry.get('id')) != page:
                    continue
                for event in entry.get('messaging', []):
                    message = event.get('message', {})
                    sender = event.get('sender', {}).get('id')
                    recipient = event.get('recipient', {}).get('id')
                    mid, text = message.get('mid'), message.get('text')
                    received = min(float(event.get('timestamp', time.time() * 1000)) / 1000, time.time())
                    if (message.get('is_echo') and str(sender) == page and recipient and str(recipient) != page
                        and (not message.get('app_id') or str(message['app_id']) in manual_apps)
                        and not message.get('metadata')
                        and isinstance(mid, str) and isinstance(text, str)):
                        command = ' '.join(text.casefold().split())
                        if command in ('pause ai', 'resume ai'):
                            events.append(('control', str(recipient), 'manual' if command == 'pause ai' else 'auto', mid, received))
                        continue
                    if (message.get('is_echo') or not sender or str(sender) == page or
                        str(event.get('recipient', {}).get('id')) != page):
                        continue
                    mid, text = message.get('mid'), message.get('text')
                    attachments = extract_attachments(message.get('attachments', []))
                    if isinstance(mid, str) and ((isinstance(text, str) and text.strip()) or attachments):
                        text = text if isinstance(text, str) and text.strip() else ''
                        events.append(('message', mid, str(sender), text, received, attachments))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, 'Invalid event payload') from None
        for event in events:
            async with request.app.state.conversation_lock:
                if event[0] == 'control':
                    await asyncio.to_thread(request.app.state.store.set_mode, *event[1:])
                else:
                    _, mid, sender, text, received, attachments = event
                    if attachments:
                        await asyncio.to_thread(request.app.state.store.enqueue, mid, sender, text, received, attachments)
                    else:
                        await asyncio.to_thread(request.app.state.store.enqueue, mid, sender, text, received)
                    if requests_person(text):
                        await asyncio.to_thread(request.app.state.store.set_mode, sender, 'manual',
                                                'handoff:' + mid, received)
        return 'EVENT_RECEIVED'

    return app


app = create_app()
