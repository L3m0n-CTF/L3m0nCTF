"""Start the beginner site: the author's app, behind the safety net in guard.py, served by waitress. The image runs `python serve.py` from /app."""
import guard

guard.install()  # before the site is imported: its own `import sqlite3` then finds the guarded connect

from waitress import serve  # noqa: E402
from app import app  # noqa: E402

serve(app, host="0.0.0.0", port=8000, threads=8, connection_limit=200)
