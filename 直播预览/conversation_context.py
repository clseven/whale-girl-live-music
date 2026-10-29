"""Provider-independent conversation history, persisted by SQLite with room isolation."""
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class ConversationContext:
    def __init__(self, root, window=12):
        self.path = Path(root) / '.runtime/conversation-context.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.window = window
        self.lock = threading.RLock()
        with self._connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    event_id TEXT,
                    viewer_id TEXT NOT NULL,
                    nickname TEXT NOT NULL,
                    event TEXT NOT NULL,
                    user TEXT NOT NULL,
                    assistant TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS turns_room ON turns(room_id, id);
                CREATE UNIQUE INDEX IF NOT EXISTS turns_event
                    ON turns(room_id, event_id) WHERE event_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            # The old JSON lacks room/viewer identity. Preserve it in a separate scope;
            # never assign old test/other-room messages to a current real viewer.
            if not db.execute("SELECT 1 FROM metadata WHERE key='legacy_imported'").fetchone():
                legacy = self.path.parent / 'model-history.json'
                if legacy.exists():
                    try:
                        rows = json.loads(legacy.read_text(encoding='utf-8'))
                    except (OSError, ValueError):
                        rows = []  # Keep the original file untouched for inspection.
                    for turn in rows if isinstance(rows, list) else []:
                        if isinstance(turn, dict) and all(isinstance(turn.get(k), str) for k in ('event', 'user', 'assistant')):
                            db.execute('''INSERT INTO turns
                                (room_id, viewer_id, nickname, event, user, assistant, provider, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                                ('__legacy__', '', '未知观众', turn['event'], turn['user'], turn['assistant'], 'legacy', 0))
                db.execute("INSERT INTO metadata VALUES ('legacy_imported', '1')")

    def _connect(self):
        # A short-lived connection per operation works in both HTTP and model threads.
        # The SQLite connection context manager commits/rolls back; closing is explicit.
        return _transaction(self.path)

    def recent(self, room_id):
        with self.lock, self._connect() as db:
            rows = db.execute('''SELECT room_id, viewer_id, nickname, event, user,
                assistant, provider, created_at FROM turns WHERE room_id=?
                ORDER BY id DESC LIMIT ?''', (str(room_id), self.window)).fetchall()
        return [{'room_id': row[0], 'viewer': {'id': row[1], 'nickname': row[2]},
                 'event': row[3], 'user': row[4], 'assistant': row[5],
                 'provider': row[6], 'created_at': row[7]} for row in reversed(rows)]

    def append(self, room_id, viewer, event, text, reply, provider, event_id=None):
        viewer = viewer or {}
        with self.lock, self._connect() as db:
            db.execute('''INSERT OR IGNORE INTO turns
                (room_id, event_id, viewer_id, nickname, event, user, assistant, provider, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                (str(room_id), str(event_id) if event_id else None,
                 str(viewer.get('id') or ''), str(viewer.get('nickname') or '朋友'),
                 event, text, reply, provider, time.time()))

    def clear(self):
        with self.lock, self._connect() as db:
            db.execute('DELETE FROM turns')


@contextmanager
def _transaction(path):
    db = sqlite3.connect(path, timeout=5)
    try:
        with db:
            yield db
    finally:
        db.close()
