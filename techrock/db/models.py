"""SQLAlchemy models matching the existing MySQL tables."""
from sqlalchemy import BigInteger, Index, Integer, String, Text
from sqlalchemy.dialects.mysql import DOUBLE, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Conversation(Base):
    __tablename__ = 'conversations'
    sender: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), default='auto')
    changed: Mapped[float] = mapped_column(DOUBLE(asdecimal=False), default=0)

class ControlEvent(Base):
    __tablename__ = 'control_events'
    mid: Mapped[str] = mapped_column(VARCHAR(600, charset='ascii', collation='ascii_bin'), primary_key=True)

class Job(Base):
    __tablename__ = 'jobs'
    __table_args__ = (Index('queue_lookup', 'status', 'next_try'), Index('sender_order', 'sender', 'status', 'id'), {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    mid: Mapped[str] = mapped_column(VARCHAR(512, charset='ascii', collation='ascii_bin'), unique=True)
    sender: Mapped[str] = mapped_column(String(128))
    text: Mapped[str] = mapped_column(Text)
    received: Mapped[float] = mapped_column(DOUBLE(asdecimal=False))
    status: Mapped[str] = mapped_column(String(16), default='pending', server_default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default='0')
    next_try: Mapped[float] = mapped_column(DOUBLE(asdecimal=False), default=0, server_default='0')
    reply: Mapped[str | None] = mapped_column(Text)

class History(Base):
    __tablename__ = 'history'
    __table_args__ = (Index('conversation', 'sender', 'id'), {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4'})
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sender: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)

class JobMedia(Base):
    """Separate table keeps existing jobs installations compatible without ALTER."""
    __tablename__ = 'job_media'
    mid: Mapped[str] = mapped_column(VARCHAR(512, charset='ascii', collation='ascii_bin'), primary_key=True)
    attachments: Mapped[str] = mapped_column(Text)
    prepared_text: Mapped[str | None] = mapped_column(Text)
