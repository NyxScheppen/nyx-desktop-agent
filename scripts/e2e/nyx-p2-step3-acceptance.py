import asyncio
import importlib.util
import json
import logging
import sys
import tempfile
import uuid
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path.cwd()))
from nyx import db
from nyx.activity.facade import ActivityFacade
from nyx.activity.store import ActivityStore
from nyx.config import ActivityConfig, ExplorationConfig
from nyx.enums import AssignedTaskStatus, AssignedTaskType
from nyx.types import AssignedTask

logging.disable(logging.CRITICAL)
spec = importlib.util.spec_from_file_location('step2', Path(__file__).with_name('nyx-p2-step2-acceptance.py'))
helper = importlib.util.module_from_spec(spec)
exec(Path(spec.origin).read_text(encoding='utf-8').rsplit('asyncio.run(main())', 1)[0], helper.__dict__)


def task(checkpoint=None, task_id=None):
    return AssignedTask(task_id or uuid.uuid4().hex, AssignedTaskType.WEB, AssignedTaskStatus.RUNNING,
                        'https://example.com/source', None, None, checkpoint or {}, None, 1.0, 1.0)


def facade(database, memory, llm, fetch):
    async def unused():
        raise AssertionError('unused dependency')
    return ActivityFacade(ActivityStore(database), memory._bus, llm, helper.Evaluator(), None, None,
                          memory, unused, unused, ActivityConfig(), ExplorationConfig(web_enabled=True),
                          'acceptance', fetch_source=fetch)


async def large_source():
    database, ms, facts, memory, llm = await helper.stack(points=1)
    text = ''.join(f'block{i:02d} ' + str(i % 10) * 5992 for i in range(34))[:200000]
    fetched = 0
    async def fetch(**kwargs):
        nonlocal fetched
        fetched += 1
        return {'text': text}
    runner = facade(database, memory, llm, fetch)
    store = runner._store
    assigned = await store.create_task(task())
    checkpoints = []
    original = store.save_task
    async def save(candidate):
        checkpoints.append(json.loads(json.dumps(candidate.checkpoint)))
        await original(candidate)
    statements = []
    await database.conn.set_trace_callback(statements.append)
    try:
        with patch.object(store, 'save_task', side_effect=save):
            await runner._run_web_task(assigned)
        assert all('source_text' not in cp for cp in checkpoints), 'full body still rewritten in checkpoint'
        writes = [s for s in statements if s.startswith('INSERT INTO assigned_task_source')]
        assert len(writes) == 1, len(writes)
        assert await store.get_task_source(assigned.id) == text
        assert fetched == 1 and assigned.checkpoint['cursor'] == 200000
        assert len(checkpoints) == 102, len(checkpoints)
        assert max(len(cp.get('pending', {}).get('source_text', '')) if cp.get('pending') else 0 for cp in checkpoints) == 6000
        assert len([kind for kind, _ in llm.calls if kind == 'source_digest']) == 34
        chunks = await ms.list_knowledge_chunks()
        assert len(chunks) == 34, len(chunks)
        saved_facts = await facts.recent(scope='knowledge', limit=100)
        assert len(saved_facts) == 34 and all(f.source_chunk_id in {c.id for c in chunks} for f in saved_facts)
        print(json.dumps({'scenario': 'large_source', 'body_characters': len(text), 'snapshot_writes': len(writes),
                          'checkpoint_writes': len(checkpoints), 'max_checkpoint_characters': max(len(json.dumps(cp, ensure_ascii=False)) for cp in checkpoints),
                          'chunks': len(chunks), 'facts': len(saved_facts)}), flush=True)
    finally:
        await database.close()


async def stack_at(path, points=2):
    database = await db.connect(str(path))
    ms = helper.MemoryStore(database)
    facts = helper.MemoryFactStore(database)
    llm = helper.ScriptedLlm(points)
    memory = helper.MemoryFacade(ms, helper.MemoryRetrieval(ms, None), helper.EventBus(database),
                                 llm, helper.Evaluator(), helper.MemoryConfig(), None, facts)
    return database, ms, facts, memory, llm


async def raises(operation, error=RuntimeError):
    try:
        await operation()
    except error:
        return
    raise AssertionError('expected injected failure')


async def failure_replay():
    for stage in ('source_insert', 'source_commit', 'pending_save', 'pending_commit', 'evidence',
                  'digest', 'extraction_save', 'extraction_commit', 'knowledge_mid', 'final_save', 'final_commit'):
        with tempfile.TemporaryDirectory(prefix='nyx-step3-') as directory:
            path = Path(directory) / 'character.db'
            database, ms, facts, memory, llm = await stack_at(path)
            original_text = ('original ' + 'A' * 5991) + ('tail-original ' + 'B' * 300)
            remote = original_text
            fetches = 0
            async def fetch(**kwargs):
                nonlocal fetches
                fetches += 1
                return {'text': remote}
            runner = facade(database, memory, llm, fetch)
            store = runner._store
            assigned = await store.create_task(task())
            original_save = store.save_task
            original_commit = database.conn.commit
            original_source = store.save_task_source
            original_evidence = memory.record_reading_evidence
            original_add = ms.add
            hits = 0
            def matches(cp):
                pending = cp.get('pending')
                if stage.startswith('pending'):
                    return isinstance(pending, dict) and 'knowledge' not in pending
                if stage.startswith('extraction'):
                    return isinstance(pending, dict) and 'knowledge' in pending
                return pending is None and cp.get('cursor', 0) > 0
            async def failed_save(candidate):
                if matches(candidate.checkpoint):
                    if stage.endswith('_save'):
                        raise RuntimeError(stage)
                    await original_save(candidate)
                    with patch.object(database.conn, 'commit', side_effect=RuntimeError(stage)):
                        await database.conn.commit()
                return await original_save(candidate)
            async def failed_source(*args):
                await original_source(*args)
                raise RuntimeError(stage)
            async def failed_evidence(*args, **kwargs):
                await original_evidence(*args, **kwargs)
                raise RuntimeError(stage)
            async def failed_add(*args, **kwargs):
                nonlocal hits
                hits += 1
                if hits == 2:
                    raise RuntimeError(stage)
                return await original_add(*args, **kwargs)
            try:
                with ExitStack() as patches:
                    if stage == 'source_insert':
                        patches.enter_context(patch.object(store, 'save_task_source', side_effect=failed_source))
                    elif stage == 'source_commit':
                        patches.enter_context(patch.object(database.conn, 'commit', side_effect=RuntimeError(stage)))
                    elif stage == 'evidence':
                        patches.enter_context(patch.object(memory, 'record_reading_evidence', side_effect=failed_evidence))
                    elif stage == 'digest':
                        llm.fail_digest = 'original'
                    elif stage == 'knowledge_mid':
                        patches.enter_context(patch.object(ms, 'add', side_effect=failed_add))
                    else:
                        patches.enter_context(patch.object(store, 'save_task', side_effect=failed_save))
                    await raises(lambda: runner._run_web_task(assigned))
                saved = await store.get_task(assigned.id)
                assert saved.checkpoint == assigned.checkpoint, (stage, saved.checkpoint, assigned.checkpoint)
                assert saved.checkpoint.get('cursor', 0) == 0
                assert not database.conn.in_transaction
                source = await store.get_task_source(assigned.id)
                assert (source is None) == stage.startswith('source_'), stage
                if stage == 'knowledge_mid':
                    assert await helper.row_count(database, 'memory') == 1
                # Persisted stage is all the new process receives.
                await database.close()
                database, ms, facts, memory, llm = await stack_at(path)
                remote = ('changed ' + 'Z' * 5992) + ('tail-changed ' + 'Y' * 300)
                runner = facade(database, memory, llm, fetch)
                store = runner._store
                saved = await store.get_task(assigned.id)
                await runner._run_web_task(saved)
                expected = remote if stage.startswith('source_') else original_text
                assert await store.get_task_source(assigned.id) == expected
                assert fetches == (2 if stage.startswith('source_') else 1), (stage, fetches)
                assert saved.checkpoint['cursor'] == len(expected) and saved.checkpoint['pending'] is None
                chunks = await ms.list_knowledge_chunks()
                assert ''.join(c.content for c in sorted(chunks, key=lambda c: c.created_at)) == expected, stage
                assert await helper.row_count(database, 'memory') == 4, stage
                assert await helper.row_count(database, 'memory_fact') == 4, stage
                # Extracted pending must not be digested again after restart.
                digests = [json.loads(user)['source_text'] for kind, user in llm.calls if kind == 'source_digest']
                expected_digests = 1 if stage in ('knowledge_mid', 'final_save', 'final_commit') else 2
                assert len(digests) == expected_digests, (stage, digests)
                print('PASS failure/restart/changed remote: ' + stage, flush=True)
            finally:
                await database.close()


async def migration_and_isolation():
    with tempfile.TemporaryDirectory(prefix='nyx-step3-migration-') as directory:
        path = Path(directory) / 'old.db'
        database = await db.connect(str(path), migrations=[m for m in db._MIGRATIONS if m[0] <= 44])
        store = ActivityStore(database)
        originals = {}
        for i, status in enumerate(AssignedTaskStatus):
            pending = {'cursor': 6000, 'source_text': 'legacy raw', 'knowledge': [], 'profile': {'summary': 'old'}}
            item = task({'source_text': 'legacy ' + str(i), 'cursor': 0, 'profile': {'key': 1}, 'pending': pending})
            item.status = status
            item.url += str(i)
            originals[item.id] = deepcopy(item.checkpoint)
            await store.create_task(item)
        for value in ('', 'X' * 200001, 'before\0after', '\0' + '汉🌕' * 100001, None, 17):
            item = task({'source_text': value, 'pending': None})
            item.status = AssignedTaskStatus.COMPLETED
            originals[item.id] = deepcopy(item.checkpoint)
            await store.create_task(item)
        other = task({'source_text': 'do not move', 'pending': {'source_text': 'keep'}})
        other.type = AssignedTaskType.BROWSER_SURVEY
        await store.create_task(other)
        original_execute = database.conn.execute
        async def fail_migration(sql, *args, **kwargs):
            if sql.startswith('UPDATE assigned_task SET checkpoint = json_remove'):
                raise helper.db.aiosqlite.OperationalError('injected migration failure')
            return await original_execute(sql, *args, **kwargs)
        try:
            with patch.object(database.conn, 'execute', side_effect=fail_migration):
                await raises(lambda: db.migrate(database.conn), helper.db.aiosqlite.OperationalError)
            assert (await (await database.conn.execute('SELECT version FROM schema_version')).fetchone())[0] == 44
            assert not await (await database.conn.execute("SELECT name FROM sqlite_master WHERE name='assigned_task_source'")).fetchall()
            for item_id, cp in originals.items():
                assert (await store.get_task(item_id)).checkpoint == cp
            await db.migrate(database.conn)
            await db.migrate(database.conn)
            for item_id, cp in originals.items():
                saved = await store.get_task(item_id)
                expected = deepcopy(cp)
                source = expected.get('source_text')
                if isinstance(source, str):
                    expected.pop('source_text')
                assert saved.checkpoint == expected
                assert await store.get_task_source(item_id) == (source[:200000] if isinstance(source, str) else None)
            assert (await store.get_task(other.id)).checkpoint == other.checkpoint
            assert await store.get_task_source(other.id) is None
            first = next(iter(originals))
            assert await store.save_task_source(first, 'CHANGED') == originals[first]['source_text']
            # Same URL, new task and separate database have no implicit shared source.
            another = task()
            another.status = AssignedTaskStatus.COMPLETED
            await store.create_task(another)
            assert await store.get_task_source(another.id) is None
            assert await store.save_task_source(another.id, 'separate task') == 'separate task'
            async with database.transaction():
                await database.conn.execute('DELETE FROM assigned_task WHERE id=?', (first,))
            assert await store.get_task_source(first) is None
            await raises(lambda: store.save_task_source('missing task', 'no orphan'), helper.db.aiosqlite.IntegrityError)
            assert not database.conn.in_transaction
            await raises(lambda: store.save_task_source(another.id, 'X' * 200001), ValueError)
            await raises(lambda: store.save_task_source(another.id, 'a\0' + 'b' * 200000), ValueError)
            assert await store.get_task_source(another.id) == 'separate task'
            isolated = await db.connect(':memory:')
            try:
                assert await ActivityStore(isolated).get_task_source(another.id) is None
            finally:
                await isolated.close()
            # Standalone save and caller-owned transaction both roll back on cancellation/failure.
            fresh = task()
            fresh.status = AssignedTaskStatus.COMPLETED
            await store.create_task(fresh)
            with patch.object(database.conn, 'commit', side_effect=asyncio.CancelledError()):
                await raises(lambda: store.save_task_source(fresh.id, 'rollback'), asyncio.CancelledError)
            assert await store.get_task_source(fresh.id) is None and not database.conn.in_transaction
            try:
                async with database.transaction():
                    await store.save_task_source(fresh.id, 'rollback outer')
                    raise RuntimeError('rollback')
            except RuntimeError:
                pass
            assert await store.get_task_source(fresh.id) is None
            assert await store.save_task_source(fresh.id, 'before\0after') == 'before\0after'
            print('PASS v44 migration, atomic rollback, idempotence, isolation, first write, cascade, validation and cancellation', flush=True)
        finally:
            await database.close()


async def legacy_pending():
    for raw in (False, True):
        database, ms, facts, memory, llm = await helper.stack(points=1)
        fetches = 0
        async def fetch(**kwargs):
            nonlocal fetches
            fetches += 1
            return {'text': 'changed'}
        runner = facade(database, memory, llm, fetch)
        pending = {'cursor': 7, 'profile': {}, 'knowledge': [{'topic': 'old', 'content': 'old knowledge'}]}
        if raw:
            pending.update(source_text='old raw', source_name='old', block_key='0:7')
        assigned = await runner._store.create_task(task({'pending': pending}))
        try:
            with patch.object(memory, 'remember_knowledge', wraps=memory.remember_knowledge) as remember:
                await runner._run_web_task(assigned)
                assert remember.call_args.kwargs['source_chunk_id'] is None
            assert not [k for k, _ in llm.calls if k == 'source_digest']
            saved_facts = await facts.recent(scope='knowledge')
            assert saved_facts and all(f.source_chunk_id is None for f in saved_facts)
            assert fetches == 1 and assigned.checkpoint['cursor'] == 7
        finally:
            await database.close()
    print('PASS legacy pending replay before fetch and no guessed provenance', flush=True)


async def main():
    await large_source()
    await failure_replay()
    await migration_and_isolation()
    await legacy_pending()


if __name__ == '__main__':
    asyncio.run(main())
