import asyncio
import json
import unittest
from unittest.mock import patch
import httpx
from techrock.services.media import MediaError, download, prepare_media, validate_url

class MediaTests(unittest.TestCase):
    def test_download_urls_reject_local_and_untrusted_hosts(self):
        for url in ('http://lookaside.fbsbx.com/a', 'https://127.0.0.1/a',
                    'https://fbcdn.net.evil.test/a', 'https://user:pass@fbcdn.net/a',
                    'https://fbcdn.net:8000/a', 'https://fbcdn.net:invalid/a'):
            with self.assertRaises(MediaError):
                validate_url(url)

    def run_media(self, handler, attachments, text=''):
        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch.dict('os.environ', {'GROQ_API_KEY': 'test-key'}):
                    return await prepare_media(client, text, attachments)
        return asyncio.run(run())

    def test_audio_is_uploaded_and_transcribed(self):
        def handler(request):
            if request.method == 'GET':
                self.assertNotIn('authorization', request.headers)
                return httpx.Response(200, content=b'OggSvoice-data', headers={'content-type': 'audio/ogg'})
            self.assertEqual(request.url.path, '/openai/v1/audio/transcriptions')
            self.assertIn(b'whisper-large-v3-turbo', request.content)
            self.assertIn(b'voice.ogg', request.content)
            return httpx.Response(200, json={'text': 'হেডফোন সম্পর্কে বলুন'})
        result = self.run_media(handler, [{'type': 'audio', 'url': 'https://lookaside.fbsbx.com/voice'}])
        self.assertEqual(result, 'হেডফোন সম্পর্কে বলুন')

    def test_images_use_base64_and_keep_caption(self):
        def handler(request):
            if request.method == 'GET':
                return httpx.Response(200, content=b'\x89PNG\r\n\x1a\nimage')
            payload = json.loads(request.content)
            self.assertEqual(payload['model'], 'qwen/qwen3.8-27b')
            self.assertTrue(payload['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,'))
            return httpx.Response(200, json={'choices': [{'message': {'content': 'A pair of headphones'}}]})
        result = self.run_media(handler, [{'type': 'image', 'url': 'https://scontent.fbcdn.net/photo'}], 'What product is this?')
        self.assertIn('What product is this?', result)
        self.assertIn('A pair of headphones', result)

    def test_private_redirect_is_never_followed(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
        with self.assertRaises(MediaError):
            self.run_media(handler, [{'type': 'image', 'url': 'https://scontent.fbcdn.net/photo'}])
        self.assertEqual(len(calls), 1)

    def test_expired_large_invalid_and_empty_media(self):
        for response in (httpx.Response(403), httpx.Response(200, headers={'content-length': str(5 * 1024 * 1024)}),
                         httpx.Response(200, content=b'not an image'), httpx.Response(200, content=b'')):
            with self.subTest(status=response.status_code, size=len(response.content)):
                with self.assertRaises(MediaError):
                    self.run_media(lambda request: response, [{'type': 'image', 'url': 'https://scontent.fbcdn.net/photo'}])

    def test_streamed_size_limit_without_content_length(self):
        with self.assertRaises(MediaError):
            self.run_media(lambda request: httpx.Response(200, content=b'x' * (4 * 1024 * 1024 + 1)),
                           [{'type': 'image', 'url': 'https://scontent.fbcdn.net/photo'}])

    def test_too_many_attachments(self):
        with self.assertRaises(MediaError):
            self.run_media(lambda request: self.fail('Should not download'),
                           [{'type': 'image', 'url': 'https://scontent.fbcdn.net/photo'}] * 4)

    def test_provider_failure_remains_retryable(self):
        def handler(request):
            if request.method == 'GET':
                return httpx.Response(200, content=b'OggSvoice')
            return httpx.Response(503)
        with self.assertRaises(httpx.HTTPStatusError):
            self.run_media(handler, [{'type': 'audio', 'url': 'https://lookaside.fbsbx.com/voice'}])
