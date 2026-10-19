#!/usr/bin/env python3
"""Local Codex skill inventory, observed reads and confirmed skill recycling."""
from __future__ import annotations

import argparse
import errno
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import TCPServer
from urllib.parse import urlparse, parse_qs
from skill_trash import SkillTrash, TrashError

APP = Path(__file__).resolve().parent
VERSION = '1.0.1'
DETECTOR_VERSION = "3"
SOURCES = {"builtin": "Codex 内置", "local": "本地 / 自建", "installed": "下载安装", "plugin": "插件技能", "project": "项目技能"}
SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv", "references", "assets", "scripts", "tests", "test", "examples", "templates"}


class LocalHTTPServer(ThreadingHTTPServer):
    def server_bind(self):
        # HTTPServer resolves the host's FQDN while binding. A loopback-only
        # dashboard needs no DNS, which can stall on offline or CI machines.
        TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default if default is not None else {}


@contextmanager
def readonly(path):
    conn = sqlite3.connect(f"{Path(path).as_uri()}?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        yield conn
    finally:
        conn.close()


def latest_db(home, stem):
    paths = list(home.glob(f"{stem}_*.sqlite"))
    return max(paths, key=lambda p: int(re.search(r"_(\d+)\.sqlite", p.name)[1])) if paths else None


def metadata(text):
    """Read common SKILL frontmatter without executing YAML constructors."""
    if not text.startswith("---"):
        return {}
    front = text.split("---", 2)[1]
    result = {}
    lines = front.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^([\w-]+):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.groups()
        if value in {">", "|", ">-", "|-"}:
            buf = []
            for following in lines[i+1:]:
                if following and not following[0].isspace():
                    break
                buf.append(following.strip())
            value = " ".join(buf)
        result[key] = value.strip().strip('"\'')
    return result


def skill_files(root):
    visited = set()
    for current, dirs, files in os.walk(root, followlinks=True):
        resolved = str(Path(current).resolve())
        if resolved in visited:
            dirs[:] = []
            continue
        visited.add(resolved)
        dirs[:] = sorted(d for d in dirs if d not in SKIP and (not d.startswith(".") or d == ".system"))
        if "SKILL.md" in files:
            yield Path(current)/"SKILL.md"


def family(name):
    return name.split('-', 1)[0] if '-' in name else '其他'


class Catalog:
    def __init__(self, home, agents_home, extra=()):
        self.home, self.agents_home, self.extra = Path(home), Path(agents_home), extra
        self.skills, self.paths, self.aliases, self.roots = {}, {}, {}, []

    def scan(self, project_paths=()):
        self.project_paths = tuple(project_paths)
        skills, paths, aliases = {}, {}, {}
        lock = read_json(self.agents_home/".skill-lock.json").get("skills", {})
        roots = [(self.home/"skills", "local", ""), (self.agents_home/"skills", "installed", ""), (Path("/etc/codex/skills"), "builtin", "system")]
        roots += [(Path(p).expanduser(), "local", "extra") for p in self.extra]
        for raw in project_paths:
            p = Path(raw)
            if not p.is_absolute():
                continue
            for ancestor in (p, *p.parents):
                if ancestor == Path.home() or ancestor == Path("/"):
                    break
                roots += [(ancestor/".agents/skills", "project", str(ancestor)), (ancestor/".codex/skills", "project", str(ancestor))]
        seen_roots, descriptors = set(), []
        for root, source, scope in roots:
            if not root.is_dir() or str(root.resolve()) in seen_roots:
                continue
            seen_roots.add(str(root.resolve()))
            descriptors.append({"path": str(root), "source": source})
            for file in skill_files(root):
                rel = str(file.relative_to(root))
                actual_source = "builtin" if rel.startswith(".system/") else source
                logical = f"{actual_source}:{scope}:{rel}"
                self._add(skills, paths, aliases, file, logical, actual_source, "", "", lock, root=root)
        cache = self.home/"plugins/cache"
        if cache.is_dir():
            descriptors.append({"path": str(cache), "source": "plugin"})
            manifests = list(cache.glob("*/*/*/.codex-plugin/plugin.json"))
            # Sort oldest first so the newest cached copy supplies the displayed content.
            manifests.sort(key=lambda p: p.stat().st_mtime)
            for manifest in manifests:
                base = manifest.parent.parent
                marketplace, plugin, version = base.relative_to(cache).parts
                data = read_json(manifest)
                declared = data.get("skills", "./skills/")
                declared = [declared] if isinstance(declared, str) else declared if isinstance(declared, list) else []
                for directory in declared:
                    if not isinstance(directory, str):
                        continue
                    root = base/directory
                    if not root.is_dir():
                        continue
                    for file in skill_files(root):
                        rel = str(file.relative_to(root))
                        # Curated remote and old curated caches represent the same plugin.
                        market_key = marketplace.replace("-remote", "")
                        logical = f"plugin:{market_key}:{plugin}:{rel}"
                        self._add(skills, paths, aliases, file, logical, "plugin", plugin, version, lock, marketplace, root)
        # Merge symlink aliases pointing to an already inventoried skill file.
        canonical = {}
        for sid, skill in list(skills.items()):
            real = str(Path(skill["path"]).resolve())
            if real in canonical:
                keep = canonical[real]
                known_paths = {c['path'] for c in skills[keep]['copies']}
                skills[keep]['copies'].extend(c for c in skill['copies'] if c['path'] not in known_paths)
                for path, target in list(paths.items()):
                    if target == sid:
                        paths[path] = keep
                skills.pop(sid)
            else:
                canonical[real] = sid
        self.skills, self.paths, self.aliases, self.roots = skills, paths, aliases, descriptors
        return self

    def _add(self, skills, paths, aliases, file, logical, source, plugin, version, lock, marketplace="", root=None):
        try:
            text = file.read_text(encoding="utf-8")
            stat = file.stat()
        except (OSError, UnicodeError):
            return
        meta = metadata(text)
        name = meta.get("name") or file.parent.name
        sid = hashlib.sha256(logical.encode()).hexdigest()[:16]
        previous = skills.get(sid, {})
        copies = previous.get("copies", [])
        if not any(c["path"] == str(file) for c in copies):
            copies.append({"path": str(file), "root": str(root), "version": version, "modifiedAt": int(stat.st_mtime*1000)})
        origin = lock.get(file.parent.name, {})
        skills[sid] = {"id": sid, "name": name, "description": meta.get("description", ""), "source": source,
                       "sourceLabel": SOURCES[source], "plugin": plugin, "version": version,
                       "marketplace": marketplace, "path": str(file), "family": family(plugin or name),
                       "modifiedAt": int(stat.st_mtime*1000), "sourceUrl": origin.get("sourceUrl", ""),
                       "provenance": "安装记录" if origin else "插件目录" if plugin else "目录位置（无法据此判断作者）",
                       "copies": copies, "content": text, "bytes": stat.st_size}
        paths[str(file)] = paths[str(file.resolve())] = sid
        # Version-independent aliases allow historical read events to resolve after updates.
        if plugin:
            m = re.search(r"/plugins/cache/[^/]+/([^/]+)/[^/]+/(.*)$", str(file))
            if m:
                aliases[f"plugin:{m[1]}:{m[2]}"] = sid

    def resolve(self, raw, cwd=""):
        path = os.path.expanduser(raw.strip('"\''))
        if not os.path.isabs(path) and cwd:
            path = os.path.normpath(os.path.join(cwd, path))
        if path in self.paths:
            return self.paths[path]
        if os.path.isabs(path):
            try:
                canonical = str(Path(path).resolve())
                if canonical in self.paths:
                    return self.paths[canonical]
            except (OSError, RuntimeError):
                pass
        m = re.search(r"/plugins/cache/[^/]+/([^/]+)/[^/]+/(.*)$", path)
        if m:
            return self.aliases.get(f"plugin:{m[1]}:{m[2]}")
        return None


def observed_reads(command, actions, cwd, catalog):
    """Only explicit file reads. Never search tool output, chat text or patch bodies."""
    found = {}
    for action in actions or []:
        if action.get("type") == "read" and str(action.get("path", "")).endswith("SKILL.md"):
            path = action["path"]
            sid = catalog.resolve(path, cwd)
            if sid:
                found[sid] = {"path": path, "method": "文件读取"}
    # commandActions is authoritative when it classified a read. Fallback is for
    # compound shell calls whose actions are marked unknown by the Codex parser.
    # Strip heredoc bodies to avoid interpreting source code / patch content as reads.
    # Unwrap before stripping multiline scripts: quoted code is data, not a shell command.
    try:
        outer = shlex.split(command)
        if len(outer) >= 3 and Path(outer[0]).name in {"bash", "zsh", "sh"} and "c" in outer[1]:
            command = outer[2]
    except ValueError:
        pass
    lines, in_heredoc = [], None
    for line in command.splitlines():
        if in_heredoc:
            if line.strip().strip('"\'') == in_heredoc:
                in_heredoc = None
            continue
        here = re.search(r"<<-?\s*['\"]?([A-Za-z_][\w]*)", line)
        if here:
            in_heredoc = here[1]
            continue
        lines.append(line)
    shell = "\n".join(lines)
    # Recognize only read-only shell utilities; no arbitrary string/name matching.
    try:
        lexer = shlex.shlex(shell.replace('\n', ';'), posix=True, punctuation_chars=';&|()')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return found
    segments, current = [], []
    for token in tokens + [';']:
        if token and set(token) <= set(';&|()'):
            if current:
                segments.append(current)
            current = []
        else:
            current.append(token)
    for segment in segments:
        while segment and segment[0] in {'then', 'do'}:
            segment = segment[1:]
        if not segment:
            continue
        utility, args = Path(segment[0]).name, segment[1:]
        if utility not in {'cat', 'head', 'tail', 'sed', 'bat', 'less', 'more'}:
            continue
        if utility == "sed" and any(a == "-i" or a.startswith("-i") for a in args):
            continue
        for path in args:
            if path.endswith("SKILL.md") and not path.startswith("-"):
                sid = catalog.resolve(path, cwd)
                if sid:
                    found[sid] = {"path": path, "method": "文件读取"}
    return found


class Atlas:
    def __init__(self, home, agents_home, extra=(), cache_dir=None):
        self.home = Path(home)
        self.catalog = Catalog(home, agents_home, extra)
        self.lock = threading.RLock()
        self.catalog_guard = threading.RLock()
        self.snapshot = {"skills": [], "sessions": [], "links": [], "status": {"indexing": True, "indexed": 0, "total": 0, "warnings": []}}
        self.stop = threading.Event()
        self.refresh = threading.Event()
        self.cache_dir = Path(cache_dir or self.home/'skill-atlas')
        self.cache_dir.mkdir(exist_ok=True, parents=True, mode=0o700)
        self.trash = SkillTrash(self.cache_dir/'trash', protected_paths=[APP.parent])
        self.trash_snapshot = {'entries': [], 'retentionDays': 30, 'warnings': []}
        self.refresh_trash()
        self.state_db = latest_db(self.home, "state")
        self.history_db = latest_db(self.home, "thread_history")
        self.started = time.time()

    def public_skills(self):
        return [{k: v for k, v in s.items() if k != 'content'} for s in self.catalog.skills.values()]

    def refresh_trash(self):
        with self.catalog_guard:
            try:
                inventory = self.trash.maintain()
            except (TrashError, OSError) as exc:
                inventory = {**self.trash_snapshot, 'warnings': ['回收站暂时无法更新：'+str(exc)]}
            with self.lock:
                self.trash_snapshot = inventory
                self.snapshot['trash'] = inventory

    def publish(self, data):
        # A scan started before a deletion must never republish deleted nodes/edges.
        with self.lock:
            valid = set(self.catalog.skills)
            data['skills'] = self.public_skills()
            data['links'] = [l for l in data.get('links', []) if l['skillId'] in valid]
            data['events'] = [e for e in data.get('events', []) if e['skillId'] in valid]
            data['trash'] = self.trash_snapshot
            self.snapshot = data

    def rescan_after_mutation(self):
        self.refresh_trash()
        old = self.catalog
        fresh = Catalog(old.home, old.agents_home, old.extra).scan(getattr(old, 'project_paths', ()))
        with self.lock:
            self.catalog = fresh
            self.publish(dict(self.snapshot))
        self.refresh.set()

    def deletion_plan(self, sid):
        with self.catalog_guard:
            return self.trash.preview(self.catalog, sid)

    def delete_skill(self, sid, token):
        with self.catalog_guard:
            result = self.trash.delete(self.catalog, sid, token)
            self.rescan_after_mutation()
            return result | {'graph': self.snapshot}

    def batch_deletion_plan(self, ids):
        with self.catalog_guard:
            return self.trash.preview_batch(self.catalog, ids)

    def delete_skills(self, ids, token):
        with self.catalog_guard:
            result = self.trash.delete_batch(self.catalog, ids, token)
            self.rescan_after_mutation()
            return result | {'graph': self.snapshot}

    def restore_skill(self, trash_id):
        with self.catalog_guard:
            result = self.trash.restore(trash_id)
            self.rescan_after_mutation()
            return result | {'graph': self.snapshot}

    def run(self):
        cache = sqlite3.connect(self.cache_dir/"observations.sqlite")
        cache.row_factory = sqlite3.Row
        cache.executescript("""
        CREATE TABLE IF NOT EXISTS cursor (thread TEXT PRIMARY KEY, ordinal INTEGER, generation TEXT);
        CREATE TABLE IF NOT EXISTS reads (thread TEXT, item TEXT, skill TEXT, turn TEXT, time INTEGER, path TEXT, method TEXT,
          PRIMARY KEY(thread,item,skill));
        """)
        last_catalog, last_generation, warnings, last_trash = 0, "", [], 0
        while not self.stop.is_set():
            begin = time.time()
            # Cleanup runs even when the Codex databases are missing or unavailable.
            if begin-last_trash >= 60:
                self.refresh_trash()
                last_trash = begin
            try:
                if not self.state_db or not self.history_db:
                    self.state_db = latest_db(self.home, "state")
                    self.history_db = latest_db(self.home, "thread_history")
                    if not self.state_db or not self.history_db:
                        raise RuntimeError("未找到本地 Codex 会话数据库。Skill 目录仍可浏览；此版本需要 state 和 thread_history SQLite 数据库。")
                with readonly(self.state_db) as state:
                    cols = {r[1] for r in state.execute("pragma table_info(threads)")}
                    name_expr = "name" if "name" in cols else "NULL"
                    kind_expr = "thread_source" if "thread_source" in cols else "NULL"
                    threads = [dict(r) for r in state.execute(f"SELECT id,{name_expr} AS name,{kind_expr} AS kind,title,cwd,updated_at,archived FROM threads ORDER BY updated_at DESC")]
                    project_paths = [t["cwd"] for t in threads]
                    if state.execute("SELECT 1 FROM sqlite_master WHERE name='project_roots'").fetchone():
                        project_paths += [r[0] for r in state.execute("SELECT path FROM project_roots")]
                if time.time()-last_catalog > 30 or self.refresh.is_set():
                    with self.catalog_guard:
                        fresh = Catalog(self.home, self.catalog.agents_home, self.catalog.extra).scan(project_paths)
                        with self.lock:
                            self.catalog = fresh
                        last_catalog = time.time()
                        self.refresh.clear()
                catalog = self.catalog
                # Database identity + catalog identity invalidates obsolete recognition cursors.
                generation = hashlib.sha256((DETECTOR_VERSION+str(self.history_db)+str(self.history_db.stat().st_ino)+"|".join(sorted(catalog.paths))).encode()).hexdigest()
                if generation != last_generation:
                    cursors = {r["thread"]: r["ordinal"] for r in cache.execute("SELECT * FROM cursor WHERE generation=?", (generation,))}
                    last_generation = generation
                indexed = sum(t["id"] in cursors for t in threads)
                pending = [t for t in threads if t["id"] not in cursors]
                # Always process recently updated conversations first; finish older history in batches.
                batch = {t["id"]: t for t in threads[:18] + pending[:18]}
                sessions = []
                with readonly(self.history_db) as history:
                    for t in threads:
                        turn = history.execute("SELECT turn_id,status,started_at,completed_at FROM thread_turns WHERE thread_id=? ORDER BY rollout_ordinal DESC LIMIT 1", (t["id"],)).fetchone()
                        started = (turn["started_at"] or 0) if turn else 0
                        if started > 10**12:
                            started /= 1000
                        status = turn["status"] if turn else "unknown"
                        if status == "inProgress" and time.time()-max(t["updated_at"], started) > 1800:
                            status = "unconfirmed"
                        if status == "inProgress":
                            batch[t["id"]] = t
                        title = t["name"] or self.fallback_title(t["title"])
                        sessions.append({"id": t["id"], "title": title, "cwd": t["cwd"], "status": status, "kind": t["kind"] or "user",
                                         "updatedAt": t["updated_at"]*1000, "archived": bool(t["archived"]),
                                         "turnId": turn["turn_id"] if turn else "", "indexed": t["id"] in cursors})
                    for t in batch.values():
                        tid = t["id"]
                        cursor = cursors.get(tid, -1)
                        if tid not in cursors:
                            cache.execute("DELETE FROM reads WHERE thread=?", (tid,))
                        # updated_at_ordinal captures completion of previously pending commands.
                        rows = history.execute("""SELECT item_id,turn_id,created_at_ms,
                          json_extract(item_json,'$.command') AS command,
                          json_extract(item_json,'$.commandActions') AS actions,
                          json_extract(item_json,'$.status') AS status,
                          json_extract(item_json,'$.exitCode') AS exit_code,
                          json_extract(item_json,'$.cwd') AS cwd
                          FROM thread_items WHERE thread_id=? AND updated_at_ordinal>?
                          AND item_type='commandExecution'""", (tid, cursor))
                        for row in rows:
                            if row["status"] != "completed" or row["exit_code"] not in (0, None):
                                continue
                            if "SKILL.md" not in (row["command"] or "") and "SKILL.md" not in (row["actions"] or ""):
                                continue
                            reads = observed_reads(row["command"] or "", json.loads(row["actions"] or "[]"), row["cwd"] or t["cwd"], catalog)
                            for sid, evidence in reads.items():
                                cache.execute("INSERT OR REPLACE INTO reads VALUES (?,?,?,?,?,?,?)", (tid,row["item_id"],sid,row["turn_id"],row["created_at_ms"],evidence["path"],evidence["method"]))
                        top = history.execute("SELECT MAX(updated_at_ordinal) FROM thread_items WHERE thread_id=?", (tid,)).fetchone()[0]
                        cursors[tid] = top if top is not None else -1
                        cache.execute("INSERT OR REPLACE INTO cursor VALUES (?,?,?)", (tid, cursors[tid], generation))
                cache.commit()
                by_session = {t["id"]: t for t in sessions}
                links = {}
                events = []
                for r in cache.execute("SELECT * FROM reads ORDER BY time"):
                    if r["skill"] not in catalog.skills or r["thread"] not in by_session:
                        continue
                    session = by_session[r["thread"]]
                    key = (r["skill"], r["thread"])
                    active = session["status"] == "inProgress" and r["turn"] == session["turnId"]
                    link = links.setdefault(key, {"skillId": r["skill"], "sessionId": r["thread"], "count": 0, "active": False, "currentTurn": False})
                    link.update({"lastSeen": r["time"], "path": r["path"], "method": r["method"], "count": link["count"]+1,
                                 "active": link["active"] or active, "currentTurn": link["currentTurn"] or r["turn"] == session["turnId"]})
                    events.append({"skillId": r["skill"], "sessionId": r["thread"], "time": r["time"], "active": active})
                for s in sessions:
                    s["indexed"] = s["id"] in cursors
                data = {"skills": [{k:v for k,v in s.items() if k != "content"} for s in self.catalog.skills.values()],
                        "sessions": sessions, "links": list(links.values()), "events": events[-60:][::-1],
                        "status": {"indexing": any(t["id"] not in cursors for t in threads), "indexed": sum(t["id"] in cursors for t in threads),
                                   "total": len(threads), "warnings": warnings, "updatedAt": int(time.time()*1000),
                                   "scanMs": round((time.time()-begin)*1000), "roots": self.catalog.roots,
                                   "scope": "本机 Codex 会话；依据成功读取 SKILL.md 的事件识别使用。MCP 工具调用和仅提及名称不计为 Skill 调用。"}}
                self.publish(data)
                warnings = []
            except Exception as exc:
                warnings = [f"数据读取暂不可用：{exc}"]
                if not self.catalog.skills or time.time()-last_catalog > 30 or self.refresh.is_set():
                    with self.catalog_guard:
                        fresh = Catalog(self.home, self.catalog.agents_home, self.catalog.extra).scan(getattr(self.catalog, 'project_paths', ()))
                        with self.lock:
                            self.catalog = fresh
                        last_catalog = time.time()
                        self.refresh.clear()
                with self.lock:
                    self.snapshot["skills"] = [{k:v for k,v in s.items() if k != "content"} for s in self.catalog.skills.values()]
                    self.snapshot["status"].update({"warnings": warnings, "updatedAt": int(time.time()*1000), "roots": self.catalog.roots})
            self.stop.wait(max(.2, 2-(time.time()-begin)))
        cache.close()

    @staticmethod
    def fallback_title(text):
        if "## My request:" in text:
            text = text.split("## My request:")[-1]
        line = next((s.strip() for s in text.splitlines() if s.strip()), "未命名会话")
        return line[:80]


def make_handler(atlas):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            if args and str(args[1]) != "200":
                super().log_message(fmt, *args)

        def send(self, body, mime="application/json; charset=utf-8", code=200):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Loopback host validation also prevents DNS rebinding access to local records.
            host = self.headers.get("Host", "").split(":")[0]
            if host not in {"localhost", "127.0.0.1"}:
                return self.send({"error": "Local access only"}, code=403)
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self.send({"error": "Cross-origin access denied"}, code=403)
            parsed = urlparse(self.path)
            if parsed.path == '/api/health':
                return self.send({'service': 'codex-skill-atlas', 'version': VERSION,
                                  'pid': os.getpid(), 'instanceKey': getattr(atlas, 'instance_key', '')})
            if parsed.path == "/api/graph":
                if parse_qs(parsed.query).get("refresh") == ["1"]:
                    atlas.refresh.set()
                with atlas.lock:
                    return self.send(atlas.snapshot)
            if parsed.path == "/api/skill":
                sid = parse_qs(parsed.query).get("id", [""])[0]
                with atlas.lock:
                    skill = atlas.catalog.skills.get(sid)
                    return self.send(skill or {"error": "Skill 不存在或已移除"}, code=200 if skill else 404)
            files = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/graph-input.js": "graph-input.js", "/source-colors.js": "source-colors.js", "/style.css": "style.css", "/favicon.svg": "favicon.svg"}
            if parsed.path in files:
                file = APP/"web"/files[parsed.path]
                types = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
                return self.send(file.read_bytes(), types[file.suffix]+"; charset=utf-8")
            self.send({"error": "Not found"}, code=404)

        def do_POST(self):
            host = self.headers.get('Host', '')
            if host != f'127.0.0.1:{self.server.server_port}' and host != f'localhost:{self.server.server_port}':
                return self.send({'error': 'Local access only'}, code=403)
            if self.headers.get('Origin') != f'http://{host}' or self.headers.get('Sec-Fetch-Site', 'same-origin') != 'same-origin':
                return self.send({'error': '请从本机看板发起操作。'}, code=403)
            if self.headers.get_content_type() != 'application/json':
                return self.send({'error': 'JSON request required'}, code=415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 256 * 1024:
                    return self.send({'error': 'Invalid request size'}, code=400)
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError()
                route = urlparse(self.path).path
                if route == '/api/skill/batch-deletion-plan':
                    return self.send(atlas.batch_deletion_plan(body.get('ids')))
                if route == '/api/skill/batch-delete':
                    if body.get('confirmed') is not True or not isinstance(body.get('token'), str):
                        return self.send({'error': '需要先确认删除范围。'}, code=400)
                    return self.send(atlas.delete_skills(body.get('ids'), body['token']))
                if route == '/api/skill/deletion-plan':
                    if not isinstance(body.get('id'), str):
                        raise ValueError()
                    return self.send(atlas.deletion_plan(body['id']))
                if route == '/api/skill/delete':
                    if body.get('confirmed') is not True or not isinstance(body.get('id'), str) or not isinstance(body.get('token'), str):
                        return self.send({'error': '需要先确认删除范围。'}, code=400)
                    return self.send(atlas.delete_skill(body['id'], body['token']))
                if route == '/api/skill/restore':
                    return self.send(atlas.restore_skill(body.get('trashId')))
                return self.send({'error': 'Not found'}, code=404)
            except TrashError as exc:
                return self.send({'error': str(exc)}, code=exc.status)
            except (ValueError, UnicodeError):
                return self.send({'error': 'Invalid request'}, code=400)
            except OSError:
                return self.send({'error': '文件操作失败，请检查权限后重试。'}, code=500)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--codex-home", type=Path, default=Path(os.environ.get("CODEX_HOME", Path.home()/".codex")))
    parser.add_argument("--agents-home", type=Path, default=Path.home()/".agents")
    parser.add_argument("--skills-root", action="append", default=[])
    parser.add_argument('--data-dir', type=Path, help='Runtime directory, defaults to CODEX_HOME/skill-atlas')
    parser.add_argument('--fallback-port', action='store_true', help='Choose a free loopback port if the requested port is busy')
    parser.add_argument('--instance-key', default='', help=argparse.SUPPRESS)
    parser.add_argument('--ready-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--restore-deleted', metavar='TRASH_ID', help='恢复回收区中的技能，不启动服务')
    args = parser.parse_args()
    args.codex_home = args.codex_home.expanduser().resolve()
    args.agents_home = args.agents_home.expanduser().resolve()
    data_dir = (args.data_dir or args.codex_home/'skill-atlas').expanduser().resolve()
    if data_dir == APP.parent or APP.parent in data_dir.parents:
        parser.error('运行数据目录必须放在 Skill 安装目录之外。')
    os.umask(0o077)
    if args.restore_deleted:
        try:
            result = SkillTrash(data_dir/'trash').restore(args.restore_deleted)
        except TrashError as exc:
            parser.exit(1, str(exc)+'\n')
        print('已恢复：'+result['name'])
        return
    atlas = Atlas(args.codex_home, args.agents_home, args.skills_root, cache_dir=data_dir)
    atlas.instance_key = args.instance_key
    try:
        server = LocalHTTPServer(("127.0.0.1", args.port), make_handler(atlas))
    except OSError as exc:
        if not args.fallback_port or exc.errno != errno.EADDRINUSE:
            raise
        server = LocalHTTPServer(("127.0.0.1", 0), make_handler(atlas))
    url = f'http://127.0.0.1:{server.server_port}'
    if args.ready_file:
        temporary = args.ready_file.with_suffix('.tmp')
        temporary.write_text(json.dumps({'url': url, 'pid': os.getpid(), 'instanceKey': args.instance_key}))
        temporary.replace(args.ready_file)
    worker = threading.Thread(target=atlas.run, daemon=True)
    worker.start()
    print(f"Skill Atlas → {url}", flush=True)
    def stop_server(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        atlas.stop.set()
        server.server_close()
        worker.join(5)


if __name__ == "__main__":
    main()
