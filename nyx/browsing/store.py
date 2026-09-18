"""Durable browsing checkpoints; the shared DB owns every state transition."""

import hashlib
import ipaddress
import json
import logging
import re
import time
import unicodedata
from typing import Any, cast
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from uuid import uuid4

import aiosqlite

from nyx.db import Database
from nyx.types import BrowserPageSnapshot, BrowsingPage, BrowsingSession

_AUTH_SEGMENTS = frozenset(
    (
        "auth",
        "login",
        "log-in",
        "signin",
        "sign-in",
        "signup",
        "sign-up",
        "register",
        "oauth",
        "authorize",
        "sso",
        "callback",
        "password",
        "reset-password",
        "mfa",
        "2fa",
        "checkout",
        "payment",
        "billing",
        "bank",
        "banking",
        "inbox",
        "mail",
        "medical",
        "health",
        "patient",
    )
)
_AUTH_KEYS = frozenset(
    (
        "code",
        "state",
        "access_token",
        "id_token",
        "refresh_token",
        "oauth_token",
        "samlrequest",
        "samlresponse",
        "client_id",
        "redirect_uri",
        "response_type",
        "code_challenge",
    )
)
_LEASE_WHERE = "id=? AND status='integrating' AND lease_token=? AND lease_until>?"
_RAW_BYTES = (
    "length(CAST(content_text AS BLOB)) + "
    "length(CAST(focus_entries AS BLOB)) + "
    "length(CAST(COALESCE(integrated_content,'') AS BLOB)) + "
    "length(CAST(COALESCE(integrated_topics,'') AS BLOB))"
)
_CLEAR_RAW = (
    "content_text='', focus_entries='[]', integrated_content=NULL, "
    "integrated_topics=NULL"
)


def normalize_text(value: str) -> str:
    """Keep hashing, focus and LLM budgets on the same Unicode text."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _decode(value: str) -> str:
    if re.search(r"%(?![0-9a-fA-F]{2})", value):
        raise ValueError("invalid_url")
    try:
        return unquote(value, encoding="utf-8", errors="strict").casefold()
    except UnicodeDecodeError as error:
        raise ValueError("invalid_url") from error


def sensitive_url(value: str) -> bool:
    """Inspect only URL signals, never query/fragment values."""
    parts = urlsplit(value)
    if set(_decode(parts.path).split("/")) & _AUTH_SEGMENTS:
        return True
    return any(
        _decode(pair.split("=", 1)[0].replace("+", " ")) in _AUTH_KEYS
        for query in (parts.query, parts.fragment)
        for pair in query.split("&")
    )


def safe_url(value: str) -> tuple[str, str]:
    """Validate URL shape/public literal host and discard private URL parameters."""
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
        if (
            parts.scheme != "https"
            or not host
            or parts.username is not None
            or parts.password is not None
            or "\\" in value
        ):
            raise ValueError("unsafe_url")
        host = host.encode("idna").decode("ascii").lower()
        if host in ("localhost",) or host.endswith(".localhost"):
            raise ValueError("unsafe_url")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if "." not in host or host.endswith(".local"):
                raise ValueError("unsafe_url") from None
        else:
            if not address.is_global:
                raise ValueError("unsafe_url")
        netloc = f"[{host}]" if ":" in host else host
        if port is not None and port != 443:
            netloc += f":{port}"
        origin = f"https://{netloc}"
        return urlunsplit(("https", netloc, parts.path or "/", "", "")), origin
    except (UnicodeError, ValueError) as error:
        raise ValueError("unsafe_url") from error


def content_hash(title: str, text: str) -> str:
    title, text = normalize_text(title), normalize_text(text)
    return hashlib.sha256(
        f"{len(title)}:{title}{len(text)}:{text}".encode("utf-8")
    ).hexdigest()


def _page(row: aiosqlite.Row) -> BrowsingPage:
    values: dict[str, Any] = {
        key: row[key] for key in BrowsingPage.__dataclass_fields__
    }
    values["truncated"] = bool(values["truncated"])
    return BrowsingPage(**values)


def _session(row: aiosqlite.Row) -> BrowsingSession:
    return BrowsingSession(
        **{key: row[key] for key in BrowsingSession.__dataclass_fields__}
    )


def parse_focus_entries(raw: str) -> list[dict[str, str]]:
    try:
        entries: object = json.loads(raw)
        if not isinstance(entries, list) or any(
            not isinstance(item, dict)
            or not isinstance(cast(dict[str, object], item).get("focus_id"), str)
            or not isinstance(cast(dict[str, object], item).get("text"), str)
            for item in cast(list[object], entries)
        ):
            raise ValueError("invalid focus entries")
        return cast(list[dict[str, str]], entries)[:20]
    except (ValueError, TypeError):
        logging.getLogger(__name__).error("Invalid browsing focus checkpoint")
        return []


class BrowsingStore:
    """Store checkpoints and fenced worker results without owning event_log."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def _row(self, table: str, key: str) -> aiosqlite.Row:
        cursor = await self._db.conn.execute(
            f"SELECT * FROM {table} WHERE id=?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("not_found")
        return row

    async def get_session(self, session_id: str) -> BrowsingSession:
        async with self._db.lock:
            return _session(await self._row("browsing_session", session_id))

    async def get_page(self, page_id: str) -> BrowsingPage:
        async with self._db.lock:
            return _page(await self._row("browsing_page", page_id))

    async def read_snapshot(self, page_id: str) -> dict[str, Any]:
        async with self._db.lock:
            return dict(await self._row("browsing_page", page_id))

    async def list_pages(
        self, session_id: str, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[BrowsingPage], str | None]:
        if not 1 <= limit <= 100:
            raise ValueError("invalid_payload")
        async with self._db.lock:
            params: list[str | float | int] = [session_id]
            after = ""
            if cursor is not None:
                cursor_query = await self._db.conn.execute(
                    "SELECT captured_at,id FROM browsing_page "
                    "WHERE id=? AND session_id=?",
                    (cursor, session_id),
                )
                cursor_row = await cursor_query.fetchone()
                if cursor_row is None:
                    raise ValueError("invalid_payload")
                after = (
                    "AND (captured_at>? OR (captured_at=? AND id>?)) "
                )
                params.extend(
                    [cursor_row["captured_at"], cursor_row["captured_at"], cursor]
                )
            params.append(limit + 1)
            columns = ",".join(BrowsingPage.__dataclass_fields__)
            query = await self._db.conn.execute(
                f"SELECT {columns} FROM browsing_page WHERE session_id=? "
                f"{after}ORDER BY captured_at, id LIMIT ?",
                params,
            )
            rows = list(await query.fetchall())
            pages = [_page(row) for row in rows[:limit]]
            next_cursor = pages[-1].id if len(rows) > limit else None
            return pages, next_cursor

    async def get_or_create_active_session(self, now: float) -> BrowsingSession:
        async with self._db.transaction():
            await self._db.conn.execute(
                "INSERT OR IGNORE INTO browsing_session(id,started_at) VALUES (?,?)",
                (str(uuid4()), now),
            )
            cursor = await self._db.conn.execute(
                "SELECT * FROM browsing_session WHERE ended_at IS NULL"
            )
            row = await cursor.fetchone()
            if row is None:
                raise RuntimeError("active browsing session missing")
            return _session(row)

    async def _active(self, session_id: str) -> aiosqlite.Row:
        row = await self._row("browsing_session", session_id)
        if row["ended_at"] is not None:
            raise ValueError("state_conflict")
        return row

    async def _freeze(self, row: aiosqlite.Row, now: float) -> None:
        if row["status"] == "open":
            await self._db.conn.execute(
                "UPDATE browsing_page SET status='pending',frozen_at=?,updated_at=? "
                "WHERE id=? AND status='open' AND revision=?",
                (now, now, row["id"], row["revision"]),
            )
        elif row["status"] == "remembered":
            await self._db.conn.execute(
                f"UPDATE browsing_page SET {_CLEAR_RAW} WHERE id=?", (row["id"],)
            )

    async def begin_navigation(
        self, session_id: str, navigation_id: str, now: float
    ) -> tuple[BrowsingPage | None, int | None]:
        async with self._db.transaction():
            session = await self._active(session_id)
            if session["current_navigation_id"] == navigation_id:
                return None, None
            row = None
            if session["current_page_id"]:
                row = await self._row("browsing_page", session["current_page_id"])
                await self._freeze(row, now)
            await self._db.conn.execute(
                "UPDATE browsing_session SET current_navigation_id=?,"
                "current_page_id=NULL WHERE id=?",
                (navigation_id, session_id),
            )
            return (_page(row), int(row["revision"])) if row else (None, None)

    async def upsert_capture(
        self, session_id: str, snapshot: BrowserPageSnapshot, now: float
    ) -> tuple[BrowsingPage, bool]:
        url, origin = safe_url(snapshot.raw_url)
        if sensitive_url(snapshot.raw_url):
            raise ValueError("origin_authorization_required")
        canonical = url
        if snapshot.canonical_candidate:
            candidate = urljoin(snapshot.raw_url, snapshot.canonical_candidate)
            try:
                candidate_url, candidate_origin = safe_url(candidate)
                if candidate_origin == origin and not sensitive_url(candidate):
                    canonical = candidate_url
            except ValueError:
                pass
        title, text = normalize_text(snapshot.title), normalize_text(snapshot.text)
        if (
            len(snapshot.raw_url) > 8192
            or len(title) > 512
            or len(text) > 200000
            or len(snapshot.selected_text or "") > 4000
            or len(snapshot.canonical_candidate or "") > 8192
            or snapshot.capture_seq < 1
        ):
            raise ValueError("invalid_payload")
        digest = content_hash(title, text)
        async with self._db.transaction():
            session = await self._active(session_id)
            if session["current_navigation_id"] != snapshot.navigation_id:
                raise ValueError("stale_navigation")
            cursor = await self._db.conn.execute(
                "SELECT * FROM browsing_page WHERE session_id=? "
                "AND canonical_url=? AND content_hash=?",
                (session_id, canonical, digest),
            )
            row = await cursor.fetchone()
            current = (
                await self._row("browsing_page", session["current_page_id"])
                if session["current_page_id"]
                else None
            )
            if current and current["navigation_id"] != snapshot.navigation_id:
                raise ValueError("stale_navigation")
            if row and row["status"] not in ("open", "remembered"):
                raise ValueError("duplicate_page_not_ready")
            if current and snapshot.capture_seq < current["last_capture_seq"]:
                raise ValueError("stale_capture")
            if current and snapshot.capture_seq == current["last_capture_seq"]:
                return _page(current), False
            created = row is None and current is None
            if row is None:
                row = current
            elif current and row["id"] != current["id"]:
                await self._freeze(current, now)
            page_id = str(row["id"]) if row else str(uuid4())
            revision = int(row["revision"]) + 1 if row else 1
            entries = parse_focus_entries(str(row["focus_entries"])) if row else []
            selected = normalize_text(snapshot.selected_text or "")
            if selected:
                focus_id = (
                    "capture:"
                    + snapshot.navigation_id
                    + ":"
                    + hashlib.sha256(selected.encode("utf-8")).hexdigest()
                )
                if all(entry["focus_id"] != focus_id for entry in entries):
                    if len(entries) == 20:
                        raise ValueError("state_conflict")
                    entries.append({"focus_id": focus_id, "text": selected})
            incoming = len(text.encode("utf-8")) + len(
                json.dumps(entries).encode("utf-8")
            )
            old_size = 0
            if row:
                old_size = sum(
                    len(str(row[key] or "").encode("utf-8"))
                    for key in (
                        "content_text",
                        "focus_entries",
                        "integrated_content",
                        "integrated_topics",
                    )
                )
                incoming += sum(
                    len(str(row[key] or "").encode("utf-8"))
                    for key in (
                        "integrated_content",
                        "integrated_topics",
                    )
                )
            await self._ensure_capacity(incoming - old_size, now, created)
            source = "dom" if text else "metadata_only"
            if row:
                await self._db.conn.execute(
                    "UPDATE browsing_page SET navigation_id=?,last_capture_seq=?,"
                    "revision=?,url=?,canonical_url=?,origin=?,title=?,content_text=?,"
                    "content_hash=?,focus_entries=?,capture_source=?,truncated=?,"
                    "updated_at=? WHERE id=?",
                    (
                        snapshot.navigation_id,
                        snapshot.capture_seq,
                        revision,
                        url,
                        canonical,
                        origin,
                        title,
                        text,
                        digest,
                        json.dumps(entries),
                        source,
                        int(snapshot.truncated),
                        now,
                        page_id,
                    ),
                )
            else:
                await self._db.conn.execute(
                    "INSERT INTO browsing_page(id,session_id,navigation_id,"
                    "last_capture_seq,revision,url,canonical_url,origin,title,"
                    "content_text,content_hash,focus_entries,capture_source,truncated,"
                    "status,captured_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,"
                    "?,?,'open',?,?)",
                    (
                        page_id,
                        session_id,
                        snapshot.navigation_id,
                        snapshot.capture_seq,
                        revision,
                        url,
                        canonical,
                        origin,
                        title,
                        text,
                        digest,
                        json.dumps(entries),
                        source,
                        int(snapshot.truncated),
                        now,
                        now,
                    ),
                )
            await self._db.conn.execute(
                "UPDATE browsing_session SET current_page_id=? WHERE id=?",
                (page_id, session_id),
            )
            return _page(await self._row("browsing_page", page_id)), created

    async def append_focus(
        self,
        page_id: str,
        navigation_id: str,
        revision: int,
        focus_id: str,
        text: str | None,
    ) -> bool:
        async with self._db.transaction():
            row = await self._row("browsing_page", page_id)
            session = await self._active(row["session_id"])
            if (
                session["current_page_id"] != page_id
                or row["status"] != "open"
                or row["navigation_id"] != navigation_id
                or row["revision"] != revision
            ):
                raise ValueError("stale_navigation")
            entries = parse_focus_entries(row["focus_entries"])
            if any(entry["focus_id"] == focus_id for entry in entries):
                return False
            if len(entries) == 20:
                raise ValueError("state_conflict")
            entries.append({"focus_id": focus_id, "text": normalize_text(text or "")})
            encoded = json.dumps(entries)
            await self._ensure_capacity(
                len(encoded.encode("utf-8"))
                - len(str(row["focus_entries"]).encode("utf-8")),
                time.time(),
            )
            await self._db.conn.execute(
                "UPDATE browsing_page SET focus_entries=? WHERE id=?",
                (encoded, page_id),
            )
            return True

    async def freeze_page(
        self, page_id: str, navigation_id: str, revision: int, now: float
    ) -> bool:
        async with self._db.transaction():
            row = await self._row("browsing_page", page_id)
            if row["navigation_id"] != navigation_id or row["revision"] != revision:
                raise ValueError("stale_navigation")
            await self._freeze(row, now)
            await self._db.conn.execute(
                "UPDATE browsing_session SET current_page_id=NULL "
                "WHERE current_page_id=?",
                (page_id,),
            )
            return row["status"] == "open"

    async def finalize_page_outputs(
        self, page_id: str, navigation_id: str, revision: int, now: float
    ) -> bool:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "UPDATE browsing_page SET outputs_finalized=1,updated_at=? "
                "WHERE id=? AND navigation_id=? AND revision=? "
                "AND status='pending' AND outputs_finalized=0",
                (now, page_id, navigation_id, revision),
            )
            return cursor.rowcount == 1

    async def revoke_current_origin(
        self, session_id: str, origin: str, now: float
    ) -> tuple[BrowsingPage | None, int | None]:
        async with self._db.transaction():
            session = await self._active(session_id)
            cursor = await self._db.conn.execute(
                "SELECT * FROM browsing_page WHERE session_id=? AND origin=? "
                "AND navigation_id=? AND (id=? OR "
                "(status='pending' AND outputs_finalized=0)) ORDER BY captured_at DESC",
                (
                    session_id,
                    origin,
                    session["current_navigation_id"],
                    session["current_page_id"],
                ),
            )
            row = await cursor.fetchone()
            if row is None:
                return None, None
            await self._freeze(row, now)
            await self._db.conn.execute(
                f"UPDATE browsing_page SET {_CLEAR_RAW} WHERE id=?", (row["id"],)
            )
            await self._db.conn.execute(
                "UPDATE browsing_session SET current_page_id=NULL WHERE id=?",
                (session_id,),
            )
            return _page(row), int(row["revision"])

    async def claim_next(
        self, owner: str, token: str, now: float, lease_until: float
    ) -> BrowsingPage | None:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "SELECT * FROM browsing_page WHERE status IN ('pending',"
                "'pending_memory') AND outputs_finalized=1 AND available_at<=? "
                "ORDER BY frozen_at,id LIMIT 1",
                (now,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            await self._db.conn.execute(
                "UPDATE browsing_page SET status='integrating',lease_owner=?,"
                "lease_token=?,lease_until=? WHERE id=?",
                (owner, token, lease_until, row["id"]),
            )
            return _page(await self._row("browsing_page", row["id"]))

    async def renew_claim(
        self, page_id: str, token: str, now: float, lease_until: float
    ) -> bool:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                f"UPDATE browsing_page SET lease_until=? WHERE {_LEASE_WHERE}",
                (lease_until, page_id, token, now),
            )
            return cursor.rowcount == 1

    async def finish_summary(
        self,
        page_id: str,
        token: str,
        content: str,
        summary: str,
        topics: list[str],
        now: float,
    ) -> bool:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                f"SELECT id FROM browsing_page WHERE {_LEASE_WHERE} "
                "AND integrated_content IS NULL",
                (page_id, token, now),
            )
            if await cursor.fetchone() is None:
                return False
            encoded_topics = json.dumps(topics)
            await self._ensure_capacity(
                len(content.encode("utf-8")) + len(encoded_topics.encode("utf-8")), now
            )
            cursor = await self._db.conn.execute(
                "UPDATE browsing_page SET integrated_content=?,integrated_summary=?,"
                "integrated_topics=?,status='pending_memory',lease_owner=NULL,"
                "lease_token=NULL,lease_until=NULL,attempt_count=0,available_at=0 "
                f"WHERE {_LEASE_WHERE} AND integrated_content IS NULL",
                (content, summary, encoded_topics, page_id, token, now),
            )
            return cursor.rowcount == 1

    async def finish_memory(
        self, page_id: str, token: str, memory_id: str, now: float
    ) -> bool:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "UPDATE browsing_page SET status='remembered',memory_id=?,"
                "lease_owner=NULL,lease_token=NULL,lease_until=NULL,last_error=NULL "
                f"WHERE {_LEASE_WHERE} AND integrated_content IS NOT NULL",
                (memory_id, page_id, token, now),
            )
            if cursor.rowcount:
                await self._db.conn.execute(
                    f"UPDATE browsing_page SET {_CLEAR_RAW} WHERE id=? "
                    "AND NOT EXISTS(SELECT 1 FROM browsing_session "
                    "WHERE current_page_id=?)",
                    (page_id, page_id),
                )
            return cursor.rowcount == 1

    async def fail_claim(
        self,
        page_id: str,
        token: str,
        error_code: str,
        now: float,
        available_at: float,
        terminal: bool,
    ) -> bool:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "UPDATE browsing_page SET status=CASE WHEN ? OR attempt_count>=4 "
                "THEN 'failed' WHEN integrated_content IS NULL THEN 'pending' "
                "ELSE 'pending_memory' END,attempt_count=attempt_count+1,"
                "available_at=?,last_error=?,lease_owner=NULL,lease_token=NULL,"
                "lease_until=NULL,raw_retained_until=CASE WHEN ? OR attempt_count>=4 "
                f"THEN ? ELSE NULL END WHERE {_LEASE_WHERE}",
                (
                    terminal,
                    available_at,
                    error_code,
                    terminal,
                    now + 30 * 86400,
                    page_id,
                    token,
                    now,
                ),
            )
            return cursor.rowcount == 1

    async def recover_expired(self, now: float) -> int:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "UPDATE browsing_page SET status=CASE WHEN integrated_content IS NULL "
                "THEN 'pending' ELSE 'pending_memory' END,lease_owner=NULL,"
                "lease_token=NULL,lease_until=NULL WHERE status='integrating' "
                "AND lease_until<=?",
                (now,),
            )
            return cursor.rowcount

    async def close_session(self, session_id: str, now: float) -> list[BrowsingPage]:
        async with self._db.transaction():
            await self._row("browsing_session", session_id)
            cursor = await self._db.conn.execute(
                "SELECT * FROM browsing_page WHERE session_id=? "
                "AND (status='open' OR (status='pending' AND outputs_finalized=0) "
                "OR status='remembered')",
                (session_id,),
            )
            rows = await cursor.fetchall()
            for row in rows:
                await self._freeze(row, now)
            await self._db.conn.execute(
                "UPDATE browsing_session SET ended_at=COALESCE(ended_at,?),"
                "current_page_id=NULL WHERE id=?",
                (now, session_id),
            )
            return [_page(row) for row in rows if row["status"] != "remembered"]

    async def _ensure_capacity(
        self, delta: int, now: float, creating: bool = False
    ) -> None:
        # Every raw writer uses this check under the existing DB transaction.
        await self._prune(now)
        cursor = await self._db.conn.execute(
            f"SELECT COUNT(*) AS count, COALESCE(SUM({_RAW_BYTES}),0) AS size "
            "FROM browsing_page"
        )
        capacity = await cursor.fetchone()
        assert capacity is not None
        if creating and capacity["count"] >= 10000:
            raise ValueError("browsing_storage_limit")
        size = int(capacity["size"]) + delta
        if size > 50 * 1024 * 1024:
            cursor = await self._db.conn.execute(
                f"SELECT id,({_RAW_BYTES}) AS size FROM browsing_page "
                "WHERE status='failed' AND NOT EXISTS(SELECT 1 "
                "FROM browsing_session WHERE current_page_id=browsing_page.id) "
                "ORDER BY frozen_at,id"
            )
            for failed in await cursor.fetchall():
                await self._db.conn.execute(
                    f"UPDATE browsing_page SET {_CLEAR_RAW},raw_retained_until=? "
                    "WHERE id=?",
                    (now, failed["id"]),
                )
                size -= int(failed["size"]) - 2
                if size <= 50 * 1024 * 1024:
                    break
        if size > 50 * 1024 * 1024:
            raise ValueError("browsing_storage_limit")

    async def _prune(self, now: float) -> int:
        cursor = await self._db.conn.execute(
            f"UPDATE browsing_page SET {_CLEAR_RAW} WHERE status='failed' "
            "AND raw_retained_until<=? AND NOT EXISTS(SELECT 1 FROM browsing_session "
            "WHERE current_page_id=browsing_page.id)",
            (now,),
        )
        return cursor.rowcount

    async def prune_raw_snapshots(self, now: float) -> int:
        async with self._db.transaction():
            return await self._prune(now)

    async def recover_startup(self, now: float) -> None:
        """Previous-process tasks are gone, so orphan outputs can be sealed."""
        async with self._db.transaction():
            await self._db.conn.execute(
                "UPDATE browsing_page SET status='pending',frozen_at=?,updated_at=? "
                "WHERE status='open'",
                (now, now),
            )
            await self._db.conn.execute(
                "UPDATE browsing_page SET outputs_finalized=1 WHERE status='pending'"
            )
            await self._db.conn.execute(
                f"UPDATE browsing_page SET {_CLEAR_RAW} WHERE status='remembered'"
            )
            await self._db.conn.execute(
                "UPDATE browsing_session SET ended_at=COALESCE(ended_at,?),"
                "current_page_id=NULL",
                (now,),
            )
            await self._prune(now)
        await self.recover_expired(now)

    async def list_unfinalized(self) -> list[BrowsingPage]:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT * FROM browsing_page WHERE status='pending' "
                "AND outputs_finalized=0"
            )
            return [_page(row) for row in await cursor.fetchall()]

    async def retry_page(self, page_id: str, now: float) -> None:
        async with self._db.transaction():
            row = await self._row("browsing_page", page_id)
            if row["status"] != "failed":
                raise ValueError("state_conflict")
            if (
                row["raw_retained_until"] is not None
                and row["raw_retained_until"] <= now
            ):
                raise ValueError("snapshot_expired")
            await self._db.conn.execute(
                "UPDATE browsing_page SET status=CASE WHEN integrated_content IS NULL "
                "THEN 'pending' ELSE 'pending_memory' END,"
                "attempt_count=0,available_at=0,"
                "last_error=NULL,lease_owner=NULL,lease_token=NULL,lease_until=NULL "
                "WHERE id=?",
                (page_id,),
            )

    async def delete_failed_page(self, page_id: str) -> None:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "DELETE FROM browsing_page WHERE id=? AND status='failed' "
                "AND NOT EXISTS(SELECT 1 FROM browsing_session "
                "WHERE current_page_id=?)",
                (page_id, page_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("state_conflict")

    async def history_deletable(self, now: float) -> bool:
        async with self._db.lock:
            cursor = await self._db.conn.execute(
                "SELECT (SELECT COUNT(*) FROM browsing_session WHERE ended_at IS NULL) "
                "+ (SELECT COUNT(*) FROM browsing_page WHERE status='integrating' "
                "AND lease_until>?)",
                (now,),
            )
            row = await cursor.fetchone()
            return row is not None and row[0] == 0

    async def delete_history(self, now: float) -> None:
        async with self._db.transaction():
            cursor = await self._db.conn.execute(
                "SELECT 1 FROM browsing_session WHERE ended_at IS NULL UNION ALL "
                "SELECT 1 FROM browsing_page WHERE status='integrating' "
                "AND lease_until>?",
                (now,),
            )
            if await cursor.fetchone() is not None:
                raise ValueError("state_conflict")
            await self._db.conn.execute("DELETE FROM browsing_page")
            await self._db.conn.execute("DELETE FROM browsing_session")
