import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from contextlib import contextmanager

load_dotenv()
ENGINE = create_engine(os.environ["DATABASE_URL"], pool_pre_ping=True, pool_size=5, max_overflow=5)

@contextmanager
def db_tx(user_id: str | None = None):
    with ENGINE.begin() as conn:
        if user_id:
            conn.execute(text("SELECT set_config('app.current_user', :u, false)"), {"u": user_id})
        yield conn