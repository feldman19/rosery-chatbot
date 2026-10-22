"""Local employee preparation tasks. Submission never publishes or notifies externally."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
import sqlite3
from urllib.parse import urlsplit
import uuid


class TaskError(ValueError):
    pass


class TaskStore:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,data TEXT NOT NULL)')

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('BEGIN IMMEDIATE')
            with db:
                yield db
        finally:
            db.close()

    def validate(self, payload):
        if not isinstance(payload, dict) or set(payload) != {'name', 'materials_url', 'destination', 'copy', 'formats'}:
            raise TaskError('יש למלא את שדות המשימה בלבד')
        limits = {'name': 100, 'copy': 1000, 'materials_url': 1000, 'destination': 1000}
        for field, limit in limits.items():
            if not isinstance(payload[field], str) or not payload[field].strip() or len(payload[field]) > limit:
                raise TaskError('יש למלא שם, קישורים וטקסט באורך מתאים')
        for field in ('materials_url', 'destination'):
            url = urlsplit(payload[field])
            if url.scheme != 'https' or not url.hostname or url.username or url.password:
                raise TaskError('יש להזין קישור HTTPS מלא, בלי סיסמה')
        if urlsplit(payload['destination']).hostname not in ('roseryjewelry.com', 'www.roseryjewelry.com'):
            raise TaskError('קישור היעד צריך להיות באתר ROSERY')
        if payload['formats'] not in ('feed_story', 'feed', 'story'):
            raise TaskError('יש לבחור אילו גדלים מוכנים')

    def create(self, payload):
        self.validate(payload)
        task = {**payload, 'id': str(uuid.uuid4()), 'status': 'awaiting_manager', 'revision': 1,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'destination_verified': False, 'published': False,
                'note': 'נשמר לבדיקה מקומית; לא נשלחה הודעה ולא פורסמה מודעה'}
        with self._db() as db:
            db.execute('INSERT INTO tasks VALUES(?,?)', (task['id'], json.dumps(task, ensure_ascii=False)))
        return task

    def resubmit(self, task_id, payload, expected_revision):
        # Reuse validation without creating an extra persisted task.
        with self._db() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise TaskError('המשימה לא נמצאה')
        old = json.loads(row[0])
        if type(expected_revision) is not int or old.get('revision', 1) != expected_revision:
            raise TaskError('המשימה השתנתה. רעננו לפני עריכה')
        if old['status'] != 'needs_changes':
            raise TaskError('אפשר לתקן משימה שהוחזרה לתיקון בלבד')
        # validate() does not persist; create() uses the same contract.
        self.validate(payload)
        task = {**old, **payload, 'status': 'awaiting_manager', 'revision': old.get('revision', 1) + 1,
                'resubmitted_at': datetime.now(timezone.utc).isoformat(), 'published': False,
                'destination_verified': False}
        task['previous_review'] = {'note': task.pop('manager_note', ''),
                                   'at': task.pop('reviewed_at', None), 'revision': expected_revision}
        with self._db() as db:
            # Refuse a concurrent manager change rather than silently overwriting it.
            latest = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not latest or json.loads(latest[0]) != old:
                raise TaskError('המשימה השתנתה. רעננו ונסו שוב')
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False), task_id))
        return task

    def list(self):
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT data FROM tasks ORDER BY rowid DESC')]

    def review(self, task_id, status, note, expected_revision):
        if status not in ('ready_for_manager', 'needs_changes') or not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000:
            raise TaskError('יש לבחור מצב ולהוסיף הערה קצרה')
        with self._db() as db:
            row = db.execute('SELECT data FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise TaskError('המשימה לא נמצאה')
            task = json.loads(row[0])
            if type(expected_revision) is not int or task.get('revision', 1) != expected_revision:
                raise TaskError('המשימה השתנתה. רעננו לפני אישור')
            task.update(status=status, manager_note=note.strip(), reviewed_at=datetime.now(timezone.utc).isoformat())
            task['revision'] = expected_revision + 1
            db.execute('UPDATE tasks SET data=? WHERE id=?', (json.dumps(task, ensure_ascii=False), task_id))
        return task
