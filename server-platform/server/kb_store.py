# -*- coding: utf-8 -*-
'''运维知识库存储层（路由表/版本迭代/维护记录）。
版本策略：每次保存自动迭代版本，保留最近 5 版，可回滚。'''
import json
import sqlite3
import threading
import time

_SCHEMA = '''
CREATE TABLE IF NOT EXISTS kb_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    created_ts INTEGER NOT NULL,
    updated_ts INTEGER NOT NULL,
    version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS kb_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    ts INTEGER NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kbver_kb ON kb_versions(kb_id, version);
CREATE TABLE IF NOT EXISTS kb_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kb_id TEXT NOT NULL,
    ts INTEGER NOT NULL,
    actor TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_kbh_kb ON kb_history(kb_id, ts);
'''

_MAX_VERSIONS = 5

class KbStore(object):
    def __init__(self, db_path):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute('PRAGMA journal_mode=WAL')
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._seed_route_table()

    def _seed_route_table(self):
        '''幂等预置「路由表 · 关键节点」条目（ADR-022）：用户打开知识库即可维护，
        终端 netdoctor route-nodes 端点即时联动读取本条目。'''
        try:
            self.create('route-nodes', 'route_nodes', '路由表 · 关键节点',
                        json.dumps([{"match": "172.17.254.0/24",
                                     "zone": "核心交换层",
                                     "desc": "核心/汇聚交换机"}],
                                   ensure_ascii=False),
                        'system', 'init seed')
        except Exception:
            pass

    def _hist(self, cur, kb_id, actor, action, detail):
        cur.execute('INSERT INTO kb_history(kb_id,ts,actor,action,detail) VALUES(?,?,?,?,?)',
                    (kb_id, int(time.time()), actor or '-', action or '-', detail or '-'))

    def route_table(self, category=None, q=None):
        '''条目列表（category 精确过滤；q 对 title/kb_id/content 模糊匹配）。'''
        cur = self._conn.cursor()
        sql = ('SELECT kb_id,category,title,author,created_ts,updated_ts,version'
               ' FROM kb_entries WHERE 1=1')
        args = []
        if category:
            sql += ' AND category=?'
            args.append(category)
        if q:
            sql += ' AND (title LIKE ? OR kb_id LIKE ? OR content LIKE ?)'
            like = '%' + q + '%'
            args.extend([like, like, like])
        sql += ' ORDER BY updated_ts DESC'
        cur.execute(sql, args)
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def categories(self):
        cur = self._conn.cursor()
        cur.execute('SELECT DISTINCT category FROM kb_entries'
                    " WHERE category IS NOT NULL AND category != ''"
                    ' ORDER BY category')
        rows = [r['category'] for r in cur.fetchall()]
        cur.close()
        return rows

    def get(self, kb_id):
        cur = self._conn.cursor()
        cur.execute('SELECT * FROM kb_entries WHERE kb_id=?', (kb_id,))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None

    def create(self, kb_id, category, title, content, author, note):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute('SELECT id FROM kb_entries WHERE kb_id=?', (kb_id,))
            if cur.fetchone():
                cur.close()
                return None, 'kb_id exists'
            cur.execute('INSERT INTO kb_entries(kb_id,category,title,content,author,'
                        'created_ts,updated_ts,version) VALUES(?,?,?,?,?,?,?,?)',
                        (kb_id, category or '-', title, content or '-',
                         author or '-', now, now, 1))
            cur.execute('INSERT INTO kb_versions(kb_id,version,title,content,author,ts,note)'
                        ' VALUES(?,?,?,?,?,?,?)',
                        (kb_id, 1, title, content or '-', author or '-', now, note or '-'))
            self._hist(cur, kb_id, author, 'create', title)
            self._conn.commit()
            cur.close()
            return kb_id, 1

    def update(self, kb_id, title, category, content, author, note):
        now = int(time.time())
        with self._lock:
            cur = self._conn.cursor()
            cur.execute('SELECT * FROM kb_entries WHERE kb_id=?', (kb_id,))
            row = cur.fetchone()
            if not row:
                cur.close()
                return None
            if category is None:
                category = row['category']
            if title is None:
                title = row['title']
            new_ver = int(row['version']) + 1
            cur.execute('UPDATE kb_entries SET category=?,title=?,content=?,author=?,'
                        'updated_ts=?,version=? WHERE kb_id=?',
                        (category, title, content, author or '-', now, new_ver, kb_id))
            cur.execute('INSERT INTO kb_versions(kb_id,version,title,content,author,ts,note)'
                        ' VALUES(?,?,?,?,?,?,?)',
                        (kb_id, new_ver, title, content, author or '-', now, note or '-'))
            cur.execute('DELETE FROM kb_versions WHERE kb_id=? AND version<=?',
                        (kb_id, new_ver - _MAX_VERSIONS))
            self._hist(cur, kb_id, author, 'update', 'note: ' + (note or '-'))
            self._conn.commit()
            cur.close()
            return new_ver

    def delete(self, kb_id, actor):
        with self._lock:
            cur = self._conn.cursor()
            cur.execute('DELETE FROM kb_entries WHERE kb_id=?', (kb_id,))
            n = cur.rowcount
            # 版本记录随条目一并清理：避免同 kb_id 重建后版本号错乱/可回滚到已删内容
            cur.execute('DELETE FROM kb_versions WHERE kb_id=?', (kb_id,))
            self._hist(cur, kb_id, actor, 'delete', 'removed')
            self._conn.commit()
            cur.close()
            return n > 0

    def versions(self, kb_id):
        cur = self._conn.cursor()
        cur.execute('SELECT version,title,author,ts,note FROM kb_versions'
                    ' WHERE kb_id=? ORDER BY version DESC', (kb_id,))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

    def get_version(self, kb_id, version):
        cur = self._conn.cursor()
        cur.execute('SELECT * FROM kb_versions WHERE kb_id=? AND version=?', (kb_id, version))
        row = cur.fetchone()
        cur.close()
        return dict(row) if row else None

    def rollback(self, kb_id, version, actor):
        src = self.get_version(kb_id, version)
        if not src:
            return None
        cur_entry = self.get(kb_id)
        cat = cur_entry['category'] if cur_entry else '-'
        ver = self.update(kb_id, src['title'], cat, src['content'], actor,
                           'rollback from v' + str(version))
        with self._lock:
            cur = self._conn.cursor()
            self._hist(cur, kb_id, actor, 'rollback', 'to v' + str(version))
            self._conn.commit()
            cur.close()
            return ver

    def history(self, kb_id, limit=50):
        cur = self._conn.cursor()
        cur.execute('SELECT ts,actor,action,detail FROM kb_history'
                    ' WHERE kb_id=? ORDER BY ts DESC, id DESC LIMIT ?', (kb_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        cur.close()
        return rows

_kb_singletons = dict()

def get_kb(db_path):
    if db_path not in _kb_singletons:
        _kb_singletons[db_path] = KbStore(db_path)
    return _kb_singletons[db_path]
