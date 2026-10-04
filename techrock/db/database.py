"""MySQL database initialization and SQLAlchemy connection/session lifecycle."""
import os
import re
from sqlalchemy import URL, create_engine
from sqlalchemy.orm import sessionmaker
import techrock.config
from techrock.db.models import Base


class Database:
    # Create missing database/tables without changing existing data; configure pooled sessions.
    def __init__(self):
        database = os.getenv('MYSQL_DATABASE', 'tech_rock_chatbot')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}', database):
            raise ValueError('MYSQL_DATABASE must contain only letters, numbers, or underscores')
        url = URL.create('mysql+mysqlconnector', username=os.getenv('MYSQL_USER', 'root'),
            password=os.environ['MYSQL_PASSWORD'], host=os.getenv('MYSQL_HOST', 'localhost'),
            port=int(os.getenv('MYSQL_PORT', '3306')), query={'charset': 'utf8mb4'})
        options = {'use_pure': True, 'connection_timeout': 5}
        bootstrap = create_engine(url, connect_args=options, hide_parameters=True)
        try:
            with bootstrap.begin() as connection:
                # Database identifiers cannot be parameters; validate the name above.
                connection.exec_driver_sql(f'CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4')
        finally:
            bootstrap.dispose()
        self.engine = create_engine(url.set(database=database), connect_args=options,
            pool_size=4, max_overflow=0, pool_pre_ping=True, pool_recycle=1800, hide_parameters=True)
        try:
            Base.metadata.create_all(self.engine)
        except Exception:
            self.engine.dispose()
            raise
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    # Release pooled connections after the application stops using the database.
    def close(self):
        self.engine.dispose()
