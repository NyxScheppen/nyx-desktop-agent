"""Role-local behavior records, research checkpoints and immutable skill versions."""

import json
import time
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any, Literal, cast

from nyx.db import Database
from nyx.types import Skill

RecordTable = Literal["research_run", "behavior_reflection"]


class BehaviorStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, table: RecordTable, key: str) -> dict[str, Any] | None:
        async with nullcontext() if self.db.in_transaction else self.db.lock:
            cursor = await self.db.conn.execute(
                f"SELECT data FROM {table} WHERE id=?", (key,)
            )
            row = await cursor.fetchone()
        return None if row is None else cast(dict[str, Any], json.loads(row["data"]))

    async def save(
        self,
        table: RecordTable,
        data: dict[str, Any],
        *,
        insert_only: bool = False,
    ) -> None:
        async with nullcontext() if self.db.in_transaction else self.db.transaction():
            now = time.time()
            values: list[object] = [
                data["id"],
                data["status"],
                json.dumps(data, ensure_ascii=False, allow_nan=False),
                data.get("created_at", now),
                now,
            ]
            columns = "id,status,data,created_at,updated_at"
            if table == "research_run":
                columns += ",origin,correlation_id"
                values += [data["origin"], data["correlation_id"]]
            conflict = (
                "DO NOTHING"
                if insert_only
                else (
                    "DO UPDATE SET status=excluded.status, data=excluded.data, "
                    "updated_at=excluded.updated_at"
                )
            )
            await self.db.conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES "
                f"({','.join('?' for _ in values)}) ON CONFLICT(id) {conflict}",
                values,
            )

    async def list_records(
        self,
        table: RecordTable,
        limit: int = 50,
        correlation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = "", []
        if table == "research_run" and correlation_id is not None:
            where, params = " WHERE correlation_id=?", [correlation_id]
        async with nullcontext() if self.db.in_transaction else self.db.lock:
            cursor = await self.db.conn.execute(
                f"SELECT data FROM {table}{where} ORDER BY created_at DESC LIMIT ?",
                [*params, max(1, min(limit, 200))],
            )
            rows = await cursor.fetchall()
        return [cast(dict[str, Any], json.loads(row["data"])) for row in rows]

    async def experience_ids(
        self,
        target_skill: str | None = None,
        *,
        user_corrections_only: bool = False,
    ) -> set[str]:
        """Resolve ownership from durable reflections, including older experiences."""
        async with nullcontext() if self.db.in_transaction else self.db.lock:
            cursor = await self.db.conn.execute(
                "SELECT data FROM behavior_reflection "
                "WHERE status IN ('persisted', 'completed', 'failed')"
            )
            rows = await cursor.fetchall()
        records = [cast(dict[str, Any], json.loads(row["data"])) for row in rows]
        if user_corrections_only:
            records = [
                record for record in records
                if record.get("evidence", {}).get("trigger_type") == "user_correction"
                and isinstance(record["evidence"].get("user_feedback_raw"), str)
                and record["evidence"]["user_feedback_raw"].strip()
            ]
        return {
            str(record["experience_id"])
            for record in records
            if (target_skill is None or record.get("target_skill") == target_skill)
            and record.get("experience_id")
        }


class SkillStore:
    """Baseline seeding never overwrites evolved text or code-owned grants."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self.baselines: dict[str, Skill] = {}

    async def seed(self, skills: dict[str, Skill]) -> None:
        self.baselines = dict(skills)
        async with self.db.transaction():
            for skill in skills.values():
                await self.db.conn.execute(
                    "INSERT OR IGNORE INTO skill_revision "
                    "(name,version,body,tool_names,reflection_id,created_at) "
                    "VALUES (?,1,?,?,NULL,?)",
                    (
                        skill.name,
                        skill.system_fragment,
                        json.dumps(skill.tool_names),
                        time.time(),
                    ),
                )

    async def load(self, name: str) -> Skill:
        async with nullcontext() if self.db.in_transaction else self.db.lock:
            cursor = await self.db.conn.execute(
                "SELECT version,body,tool_names FROM skill_revision "
                "WHERE name=? ORDER BY version DESC LIMIT 1",
                (name,),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ValueError(f"skill 尚未初始化：{name}")
        baseline = self.baselines.get(name)
        grants = (
            baseline.tool_names
            if baseline
            else tuple(cast(list[str], json.loads(row["tool_names"])))
        )
        return Skill(name, row["body"], grants, int(row["version"]))

    async def history(self, name: str) -> list[dict[str, Any]]:
        async with self.db.lock:
            cursor = await self.db.conn.execute(
                "SELECT version,body,reflection_id,created_at FROM skill_revision "
                "WHERE name=? ORDER BY version DESC",
                (name,),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def for_execution(
        self,
        key: str,
        name: str,
        topic: str,
        role_reference: dict[str, Any] | None = None,
    ) -> Skill:
        current = await self.load(name)
        async with self.db.transaction():
            await self.db.conn.execute(
                "INSERT OR IGNORE INTO skill_execution(id,data) VALUES (?,?)",
                (
                    key,
                    json.dumps(
                        {
                            "skill_snapshot_raw": skill_snapshot(current),
                            "topic_raw": topic,
                            "role_reference_raw": role_reference or {},
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            cursor = await self.db.conn.execute(
                "SELECT data FROM skill_execution WHERE id=?",
                (key,),
            )
            row = await cursor.fetchone()
        assert row is not None
        snapshot = json.loads(row["data"])["skill_snapshot_raw"]
        return Skill(
            snapshot["name"],
            snapshot["system_fragment"],
            tuple(snapshot["tool_names"]),
            snapshot["version"],
        )

    async def execution_context(self, key: str) -> dict[str, Any] | None:
        async with self.db.lock:
            cursor = await self.db.conn.execute(
                "SELECT data FROM skill_execution WHERE id=?",
                (key,),
            )
            row = await cursor.fetchone()
        return None if row is None else cast(dict[str, Any], json.loads(row["data"]))

    async def apply(
        self,
        reflection_id: str,
        name: str,
        expected_version: int,
        body: str,
    ) -> Skill:
        """Must run in B5's transaction, after B4 durably recorded its evidence."""
        if not self.db.in_transaction:
            raise RuntimeError("skill 更新需要行为反思提交事务")
        cursor = await self.db.conn.execute(
            "SELECT status,data FROM behavior_reflection WHERE id=?",
            (reflection_id,),
        )
        row = await cursor.fetchone()
        if row is None or row["status"] != "persisted":
            raise ValueError("skill 修改必须位于已持久化行为反思下游")
        record = cast(dict[str, Any], json.loads(row["data"]))
        proposal = record["reflection"]
        if (
            record["target_skill"] != name
            or proposal["skill_change_decision"] != "update"
            or proposal["proposed_skill_text"] != body
            or record["base_skill_version"] != expected_version
        ):
            raise ValueError("skill 修改与已持久化行为反思不一致")
        if not body.strip() or len(body) > 1500:
            raise ValueError("skill 正文必须为 1–1500 字符")
        current = await self.load(name)
        if current.version != expected_version:
            raise ValueError("skill_version_conflict")
        await self.db.conn.execute(
            "INSERT INTO skill_revision "
            "(name,version,body,tool_names,reflection_id,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                name,
                current.version + 1,
                body,
                json.dumps(current.tool_names),
                reflection_id,
                time.time(),
            ),
        )
        return Skill(name, body, current.tool_names, current.version + 1)


def skill_snapshot(skill: Skill) -> dict[str, Any]:
    return asdict(skill)
