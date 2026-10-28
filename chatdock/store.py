"""Independent per-user SQLite store. Each call owns its connection/thread."""
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def data_dir():
    path = Path(os.getenv("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "ModelDockChat"
    path.mkdir(parents=True, exist_ok=True)
    return path


DEFAULT_PERSONA = "你是一位温和、坦诚的中文助手。先理解用户意图，再给出具体建议。尊重用户偏好，不假装记得未提供的事实；不知道时直接说明。"


class Store:
    def __init__(self, path=None):
        self.path = Path(path) if path else data_dir() / "chat.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS personas(id INTEGER PRIMARY KEY, name TEXT NOT NULL, prompt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, title TEXT NOT NULL, persona_id INTEGER NOT NULL,
                    summary TEXT NOT NULL DEFAULT '', parent_id INTEGER, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL, content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'complete', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, content TEXT NOT NULL, kind TEXT NOT NULL,
                    evidence TEXT NOT NULL DEFAULT '', source_session INTEGER, status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS knowledge(id INTEGER PRIMARY KEY, term TEXT NOT NULL, definition TEXT NOT NULL,
                    example TEXT NOT NULL DEFAULT '', aliases TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS learning(id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
                    input TEXT NOT NULL, output TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'running', created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id,id);
                CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status);
            """)
            if "route_id" not in {row[1] for row in db.execute("PRAGMA table_info(sessions)")}:
                db.execute("ALTER TABLE sessions ADD COLUMN route_id TEXT NOT NULL DEFAULT 'local'")
            columns = {row[1] for row in db.execute("PRAGMA table_info(memories)")}
            for name, declaration in [("score", "INTEGER NOT NULL DEFAULT 5 CHECK(score BETWEEN 1 AND 10)"),
                                      ("summary", "TEXT NOT NULL DEFAULT ''"), ("analysis", "TEXT NOT NULL DEFAULT ''")]:
                if name not in columns:
                    db.execute(f"ALTER TABLE memories ADD COLUMN {name} {declaration}")
            db.execute("UPDATE memories SET summary=content WHERE summary=''")
            if not db.execute("SELECT 1 FROM personas LIMIT 1").fetchone():
                db.execute("INSERT INTO personas(name,prompt) VALUES (?,?)", ("温和搭子", DEFAULT_PERSONA))
                db.execute("INSERT INTO personas(name,prompt) VALUES (?,?)", ("理性伙伴", "你是严谨、直接的中文讨论伙伴。区分事实与推测，指出逻辑漏洞，给出可执行的方案，不为了迎合用户而编造结论。"))

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def all(self, sql, args=()):
        with self.db() as db:
            return [dict(row) for row in db.execute(sql, args)]

    def execute(self, sql, args=()):
        with self.db() as db:
            return db.execute(sql, args).lastrowid

    def get(self, key, default=None):
        rows = self.all("SELECT value FROM settings WHERE key=?", (key,))
        if not rows:
            return default
        try:
            return json.loads(rows[0]["value"])
        except ValueError:
            return default

    def set(self, key, value):
        self.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))

    def personas(self):
        return self.all("SELECT * FROM personas ORDER BY id")

    def persona(self, ident):
        rows = self.all("SELECT * FROM personas WHERE id=?", (ident,))
        if not rows:
            raise ValueError("人格不存在。")
        return rows[0]

    def save_persona(self, ident, name, prompt):
        if not name.strip() or not prompt.strip():
            raise ValueError("人格名称和提示词不能为空。")
        if len(prompt.encode()) > 8000:
            raise ValueError("人格提示词过长，请控制在 8000 UTF-8 字节以内。")
        if ident:
            self.execute("UPDATE personas SET name=?,prompt=? WHERE id=?", (name.strip(), prompt.strip(), ident))
            return ident
        return self.execute("INSERT INTO personas(name,prompt) VALUES (?,?)", (name.strip(), prompt.strip()))

    def sessions(self):
        return self.all("SELECT * FROM sessions ORDER BY id DESC")

    def session(self, ident):
        rows = self.all("SELECT * FROM sessions WHERE id=?", (ident,))
        if not rows:
            raise ValueError("会话不存在或已删除。")
        return rows[0]

    def new_session(self, persona_id=1, title="新的对话", parent_id=None, summary="", route_id="local"):
        self.persona(persona_id)
        return self.execute("INSERT INTO sessions(title,persona_id,parent_id,summary,created,route_id) VALUES (?,?,?,?,?,?)",
                            (title, persona_id, parent_id, summary, time.time(), route_id))

    def messages(self, ident, complete_only=False):
        extra = " AND status='complete'" if complete_only else ""
        return self.all("SELECT * FROM messages WHERE session_id=?" + extra + " ORDER BY id", (ident,))

    def add_message(self, ident, role, content, status="complete"):
        return self.execute("INSERT INTO messages(session_id,role,content,status,created) VALUES (?,?,?,?,?)",
                            (ident, role, content, status, time.time()))

    def delete_session(self, ident):
        self.execute("DELETE FROM sessions WHERE id=?", (ident,))
        self.execute("DELETE FROM settings WHERE key=?", ("draft:"+str(ident),))

    def add_memory(self, content, kind="用户明确提供", evidence="", source_session=None, status="pending",
                   score=5, summary=None, analysis=""):
        if not content.strip():
            raise ValueError("记忆内容不能为空。")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
            raise ValueError("记忆评分必须是 1–10 的整数。")
        summary = content.strip() if summary is None else summary.strip()
        if not summary or len(summary.encode()) > 800 or not isinstance(analysis, str) or len(analysis.encode()) > 1600:
            raise ValueError("记忆概述不能为空且应简短；分析请控制在 1600 UTF-8 字节以内。")
        with self.db() as db:
            existing = db.execute("SELECT id FROM memories WHERE content=?", (content.strip(),)).fetchone()
            if existing:
                return existing[0]
            return db.execute("INSERT INTO memories(content,kind,evidence,source_session,status,created,score,summary,analysis) VALUES (?,?,?,?,?,?,?,?,?)",
                (content.strip(), kind, evidence, source_session, status, time.time(), score, summary, analysis)).lastrowid

    def update_memory(self, ident, summary, score, analysis):
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 10:
            raise ValueError("记忆评分必须是 1–10 的整数。")
        if not summary.strip() or len(summary.encode()) > 800 or len(analysis.encode()) > 1600:
            raise ValueError("记忆概述或分析长度无效。")
        self.execute("UPDATE memories SET content=?,summary=?,score=?,analysis=?,status='pending' WHERE id=?",
                     (summary.strip(), summary.strip(), score, analysis.strip(), ident))

    def add_knowledge(self, term, definition, example="", aliases="", source="手动", status="pending"):
        if not term.strip() or not definition.strip():
            raise ValueError("词条与释义不能为空。")
        with self.db() as db:
            old = db.execute("SELECT id FROM knowledge WHERE term=? AND definition=?", (term.strip(), definition.strip())).fetchone()
            if old:
                return old[0]
            return db.execute("INSERT INTO knowledge(term,definition,example,aliases,source,status,created) VALUES (?,?,?,?,?,?,?)",
                (term.strip(), definition.strip(), example, aliases, source, status, time.time())).lastrowid

    def items(self, table, status=None):
        if table not in {"memories", "knowledge", "learning"}:
            raise ValueError("Invalid table")
        return self.all(f"SELECT * FROM {table}" + (" WHERE status=?" if status else "") + " ORDER BY id DESC",
                        (status,) if status else ())

    def approve(self, table, ids):
        if table not in {"memories", "knowledge"}:
            raise ValueError("Invalid table")
        with self.db() as db:
            db.executemany(f"UPDATE {table} SET status='approved' WHERE id=?", [(i,) for i in ids])

    def delete_items(self, table, ids):
        if table not in {"memories", "knowledge", "learning"}:
            raise ValueError("Invalid table")
        with self.db() as db:
            db.executemany(f"DELETE FROM {table} WHERE id=?", [(i,) for i in ids])

    def export_knowledge(self):
        return {"format": "modeldock-chat-knowledge-v1", "entries": [
            {k: row[k] for k in ("term", "definition", "example", "aliases", "source")}
            for row in self.items("knowledge", "approved")]}

    def import_knowledge(self, payload):
        if not isinstance(payload, dict) or payload.get("format") != "modeldock-chat-knowledge-v1":
            raise ValueError("不是 ModelDock Chat 知识文件。")
        entries = payload.get("entries")
        if not isinstance(entries, list) or len(entries) > 5000:
            raise ValueError("词条列表无效或超过 5000 条。")
        # Validate the entire file before writing any record.
        for item in entries:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k].strip() for k in ("term", "definition")):
                raise ValueError("词条缺少有效名称或释义。")
            if any(not isinstance(item.get(k, ""), str) for k in ("example", "aliases", "source")):
                raise ValueError("词条字段必须是文本。")
            if len(json.dumps(item).encode()) > 20000:
                raise ValueError("单个词条过长。")
        for item in entries:
            self.add_knowledge(item["term"], item["definition"], item.get("example", ""), item.get("aliases", ""), "导入：" + item.get("source", ""))
        return len(entries)
