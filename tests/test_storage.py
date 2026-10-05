"""Exercise real storage transactions using an isolated SQLite database."""
import unittest
from sqlalchemy import BigInteger, create_engine, event
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from techrock.db.models import Base
from techrock.db.storage import Store

@compiles(BigInteger, 'sqlite')
def sqlite_integer(type_, compiler, **kwargs):
    return 'INTEGER'

class StorageModeTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        @event.listens_for(self.engine, 'connect')
        def collations(connection, record):
            connection.create_collation('ascii_bin', lambda a, b: (a > b) - (a < b))
        Base.metadata.create_all(self.engine)
        self.store = Store.__new__(Store)
        self.store.sessions = sessionmaker(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_pause_resume_stale_and_duplicate_controls(self):
        self.store.enqueue('one', 'user', 'Hello', 10)
        self.store.set_mode('user', 'manual', 'pause', 20)
        self.assertFalse(self.store.can_reply('one'))
        self.store.enqueue('two', 'user', 'Manual question', 21)
        self.store.set_mode('user', 'auto', 'resume', 30)
        self.store.set_mode('user', 'manual', 'pause', 20)
        self.store.set_mode('user', 'manual', 'old-pause', 25)
        self.store.enqueue('three', 'user', 'New question', 31)
        self.store.enqueue('late', 'user', 'Delayed old question', 22)
        self.assertFalse(self.store.can_reply('two'))
        self.assertFalse(self.store.can_reply('late'))
        self.assertTrue(self.store.can_reply('three'))
        self.store.fail('one', 1)
        self.assertFalse(self.store.can_reply('one'))
        self.assertEqual(self.store.history('user'), [('human', 'Hello'),
            ('human', 'Manual question'), ('human', 'Delayed old question')])

    def test_mode_persists_across_store_instances(self):
        self.store.set_mode('user', 'manual', 'pause', 20)
        reopened = Store.__new__(Store)
        reopened.sessions = self.store.sessions
        reopened.enqueue('one', 'user', 'Hello', 21)
        self.assertFalse(reopened.can_reply('one'))
        reopened.enqueue('other', 'other-user', 'Hello', 21)
        self.assertTrue(reopened.can_reply('other'))

    def test_media_and_transcription_are_cached(self):
        attachments = [{'type': 'audio', 'url': 'https://lookaside.fbsbx.com/audio'}]
        self.store.enqueue('media', 'user', '', 21, attachments)
        job = self.store.next_job()
        self.assertEqual(job['attachments'], attachments)
        self.assertIsNone(job['prepared_text'])
        self.store.save_prepared_text('media', 'Transcribed question')
        self.assertEqual(self.store.next_job()['prepared_text'], 'Transcribed question')
