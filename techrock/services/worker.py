"""Background processing of queued Messenger messages."""
import asyncio
import logging
import time
from techrock.services.chatbot import generate_reply
from techrock.services.messenger import send_reply

logger = logging.getLogger("techrock")

# Continuously process queued messages: generate a reply, send it, and save the conversation.
# Expire messages older than 23 hours and retry failures with increasing delays, up to five attempts.
async def worker(store, client, conversation_lock=None):
    lock = conversation_lock or asyncio.Lock()
    while True:
        try:
            job = await asyncio.to_thread(store.next_job)
        except Exception as exc:
            logger.error('Queue read failed (%s)', type(exc).__name__)
            await asyncio.sleep(5)
            continue
        if job is None:
            await asyncio.sleep(0.5)
            continue
        try:
            if not await asyncio.to_thread(store.can_reply, job['mid']):
                continue
            if time.time() - job['received'] > 23 * 3600:
                await asyncio.to_thread(store.expire, job['mid'])
                continue
            history = await asyncio.to_thread(store.history, job['sender'])
            reply = job['reply'] or await generate_reply(job['text'], history)
            async with lock:
                if not await asyncio.to_thread(store.can_reply, job['mid']):
                    continue
                if reply is None:
                    await asyncio.to_thread(store.set_mode, job['sender'], 'manual',
                                            'handoff:' + job['mid'], time.time())
                    logger.info('Human handoff activated; conversation is now manual')
                    continue
                await asyncio.to_thread(store.save_reply, job['mid'], reply)
                await send_reply(client, job['sender'], reply)
                await asyncio.to_thread(store.complete, job, reply)
        except Exception as exc:
            # Never log exception bodies/URLs: providers may include credentials or user text.
            attempts = job['attempts'] + 1
            logger.error('Reply processing failed (%s), attempt %s', type(exc).__name__, attempts)
            try:
                await asyncio.to_thread(store.fail, job['mid'], attempts)
            except Exception as database_error:
                logger.error('Retry update failed (%s)', type(database_error).__name__)
                await asyncio.sleep(5)
