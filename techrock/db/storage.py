"""SQLAlchemy sessions for the MySQL message queue and conversation memory."""
import time
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased
from techrock.db.database import Database
from techrock.db.models import History, Job

class Store:
    # Initialize database infrastructure; keep data operations in this class.
    def __init__(self):
        self.database = Database()
        self.sessions = self.database.sessions

    # Use a transaction per operation; ignore only duplicate message IDs.
    def enqueue(self, mid, sender, text, received):
        try:
            with self.sessions.begin() as session:
                session.add(Job(mid=mid, sender=sender, text=text, received=received))
        except IntegrityError as exc:
            if getattr(exc.orig, 'errno', None) != 1062:
                raise

    # Select a ready message in each sender's arrival order; return data independent of the session.
    def next_job(self):
        earlier = aliased(Job)
        blocked = select(earlier.id).where(earlier.sender == Job.sender,
            earlier.status == 'pending', earlier.id < Job.id).exists()
        statement = select(Job).where(Job.status == 'pending', Job.next_try <= time.time(),
            ~blocked).order_by(Job.id).limit(1)
        with self.sessions() as session:
            job = session.scalar(statement)
            return None if job is None else {c.name: getattr(job, c.name) for c in Job.__table__.columns}

    # Load the last 12 conversation entries, oldest first, for the AI prompt.
    def history(self, sender):
        with self.sessions() as session:
            rows = session.execute(select(History.role, History.content).where(History.sender == sender)
                .order_by(History.id.desc()).limit(12)).all()
            return [(role, content) for role, content in reversed(rows)]

    # Cache the generated answer so delivery retries can reuse it.
    def save_reply(self, mid, reply):
        with self.sessions.begin() as session:
            session.execute(update(Job).where(Job.mid == mid).values(reply=reply))

    # Commit delivery status and both conversation messages atomically; roll back on failure.
    def complete(self, job, reply):
        with self.sessions.begin() as session:
            session.execute(update(Job).where(Job.mid == job['mid']).values(status='done'))
            session.add_all([History(sender=job['sender'], role='human', content=job['text']),
                History(sender=job['sender'], role='ai', content=reply)])

    # Stop processing messages too old for a standard Messenger reply.
    def expire(self, mid):
        with self.sessions.begin() as session:
            session.execute(update(Job).where(Job.mid == mid).values(status='expired'))

    # Schedule a delayed retry; permanently fail the job after five attempts.
    def fail(self, mid, attempts):
        with self.sessions.begin() as session:
            session.execute(update(Job).where(Job.mid == mid).values(attempts=attempts,
                status='failed' if attempts >= 5 else 'pending',
                next_try=time.time() + min(2 ** attempts * 5, 300)))

    # Release SQLAlchemy's pooled connections when the app stops.
    def close(self):
        self.database.close()
