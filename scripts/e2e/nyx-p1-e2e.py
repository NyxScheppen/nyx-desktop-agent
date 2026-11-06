import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import patch

import httpx
import uvicorn

sys.path.insert(0, str(Path.cwd()))
from nyx.config import Config
from nyx.enums import InteractionKind, InteractionStatus
from nyx.expression.store import ExpressionInteractionStore
from nyx.main import build_app, build_app_context
from nyx.memory.facts import FactCandidate, MemoryFactStore
from nyx.memory.store import MemoryStore
from nyx.types import ConversationSegment, LLMOutput

DISCUSSION = {'memory_recall', 'knowledge_search', 'deep_research'}

class FakeLlm:
    def __init__(self):
        self.calls = []
        self.mode = 'none'
        self.rounds = defaultdict(int)
        self.memory_replay = False

    async def complete(self, messages, *, module, output_type, correlation_id, tools=None, **kwargs):
        self.calls.append({'module': module, 'type': output_type, 'cid': correlation_id,
                           'messages': messages, 'tools': tools})
        calls = []
        if module == 'expression' and output_type == 'tool':
            self.rounds[correlation_id] += 1
            n = self.rounds[correlation_id]
            if self.mode == 'lookup':
                if n == 1:
                    calls = [{'name': name, 'args': {'query': 'E2E'}}
                             for name in ['memory_recall', 'knowledge_search']]
                elif n == 2:
                    calls = [{'name': 'deep_research', 'args': {'query': 'E2E'}}]
            elif self.mode == 'unauthorized':
                calls = [{'name': 'file_io', 'args': {'action': 'write', 'path': 'must-not-exist.txt', 'content': 'bad'}}]
            elif self.mode == 'failure' and n == 1:
                calls = [{'name': 'memory_recall', 'args': {'query': 'E2E', 'unexpected': True}}]
            elif self.mode == 'round_limit':
                calls = [{'name': 'deep_research', 'args': {'query': f'E2E round {n}'}}]
            elif self.mode == 'duplicate':
                calls = [{'name': 'memory_recall', 'args': {'query': 'E2E'}}]
            elif self.mode == 'item_limit':
                calls = [{'name': 'deep_research', 'args': {'query': f'E2E item {i}'}} for i in range(8)]
        if output_type == 'reason':
            content = {'summary': '按当前输入与结果继续。', 'next_action': 'answer' if '[工作摘要' in messages[-1]['content'] else 'tools'}
        elif output_type == 'reply':
            content = {'think': '检查资料来源。', 'speak': 'E2E 回复已送达。来源：segment-e2e。', 'needs_continuation': False}
        else:
            content = {'entities': [], 'facts': [], 'summary': 'E2E', 'topics': [], 'importance': 'normal', 'evidence_event_ids': []}
        if self.memory_replay and output_type == 'fact_extraction':
            # Fixture responses exercise attribution/persistence, not model text quality.
            source = messages[-1]['content']
            content['facts'] = [
                {'subject': '用户', 'predicate': predicate, 'object': value,
                 'mode': mode, 'asserted': True}
                for phrase, predicate, value, mode in [
                    ('用户：换个话题，我在北京', '当前所在地', '北京', 'functional'),
                    ('我喜欢米饭', '喜欢', '米饭', 'multi'),
                ] if phrase in source
            ]
        return LLMOutput(module=module, type=output_type, model='scripted-e2e',
                         content=json.dumps(content, ensure_ascii=False), correlation_id=correlation_id,
                         tool_calls=calls, call_id=str(uuid.uuid4()), prompt_messages=messages)

async def embed(text):
    return [1.0, 0.0]

async def main():
    os.environ['NYX_DB'] = ':memory:'
    os.environ.pop('NYX_LAUNCH_NONCE', None)
    fake = FakeLlm()
    config = Config()
    config.expression.slow_threshold = 99.0
    config.expression.slow_max_rounds = 1
    with patch('nyx.app_context.LlmClient.from_config', return_value=fake), patch('nyx.app_context.build_embed', return_value=embed):
        app = await build_app_context(config)
    store = MemoryStore(app.database)
    now = time.time()
    await store.add_conversation_segment(ConversationSegment(
        id='segment-e2e', previous_id=None, started_at=now - 4000, ended_at=now - 3990,
        raw_event_ids=[], raw_content='用户：E2E 我喜欢研究天文。', summary='E2E',
        embedding=[1.0, 0.0], evaluated_at=now, facts_extracted_at=now))
    facts = MemoryFactStore(app.database)
    for scope in ['profile', 'conversation', 'knowledge']:
        await facts.apply([FactCandidate(subject='E2E', predicate='记录', object_value=f'{scope}-marker',
                                         valid_from=now - 1, valid_until=None)], None, scope=scope)
    bus_task = asyncio.create_task(app.bus.run())
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(64)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(build_app(app), log_level='error', lifespan='off'))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    rows = []
    reader = None
    try:
        async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', headers={'Host': '127.0.0.1:8000'}, timeout=20) as client:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            async with client.stream('GET', '/api/events') as stream:
                assert stream.status_code == 200
                events = asyncio.Queue()
                async def consume():
                    event_type = ''
                    async for line in stream.aiter_lines():
                        if line.startswith('event: '):
                            event_type = line[7:]
                        elif line.startswith('data: '):
                            await events.put((event_type, json.loads(line[6:])))
                reader = asyncio.create_task(consume())
                async def send(label, message, discussion, mode='none'):
                    fake.mode = mode
                    payload = {'message': message}
                    if discussion != 'omit':
                        payload['discussion'] = discussion
                    response = await client.post('/api/chat', json=payload)
                    assert response.status_code == 200, response.text
                    cid = response.json()['event_id']
                    async def wait_speak():
                        while True:
                            kind, data = await events.get()
                            if kind == 'speak' and data['correlation_id'] == cid:
                                assert data['content'].startswith('E2E 回复'), data
                                return data
                    await asyncio.wait_for(wait_speak(), 15)
                    calls = [call for call in fake.calls if call['cid'] == cid and call['module'] == 'expression']
                    tool = [call for call in calls if call['type'] == 'tool']
                    reply = [call for call in calls if call['type'] == 'reply']
                    assert len(reply) == 1
                    rows.append({'scenario': label, 'sse': 'speak', 'tool_rounds': len(tool)})
                    print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
                    return tool, reply[0]

                for message in ['今天能陪我聊聊吗？', '我不想讨论这个，抱抱我']:
                    tool, reply = await send('companion_auto', message, 'omit')
                    assert not tool and '[讨论范式]' not in reply['messages'][0]['content']
                tool, reply = await send('forced_off', '宇宙为什么会膨胀', False)
                assert not tool and '[工具与引用纪律]' not in reply['messages'][0]['content']
                for label, message, flag in [('automatic', '宇宙为什么会膨胀', None), ('forced_on', '嗯', True)]:
                    before = (await store.get_conversation_segment('segment-e2e')).recall_count
                    tool, reply = await send(label, message, flag, 'lookup')
                    assert len(tool) == 3
                    for call in tool:
                        names = {s['function']['name'] for s in call['tools']}
                        assert names == DISCUSSION | {'replan'}
                        assert all(
                            s['function']['description']
                            and (
                                s['function']['parameters']['required'] == ['query']
                                if s['function']['name'] != 'replan'
                                else s['function']['parameters']['required'] == ['reason']
                            )
                            for s in call['tools']
                        )
                    first_system = tool[0]['messages'][0]['content']
                    assert '[讨论范式]' in first_system and '[历史对话段]' not in tool[0]['messages'][1]['content']
                    assert 'profile-marker' not in first_system and 'knowledge-marker' not in first_system
                    second_user = tool[1]['messages'][1]['content']
                    assert 'segment-e2e' in second_user and 'profile-marker' in second_user and 'conversation-marker' in second_user
                    assert 'knowledge-marker' in second_user and 'source_chunk_id' in second_user
                    memory_json = second_user.split('memory_recall: ', 1)[1].split(
                        '\n- knowledge_search:', 1
                    )[0]
                    assert 'knowledge-marker' not in memory_json
                    assert '联网没开' in tool[2]['messages'][1]['content']
                    assert 'segment-e2e' in reply['messages'][0]['content'] and '[工具与引用纪律]' in reply['messages'][0]['content']
                    assert (await store.get_conversation_segment('segment-e2e')).recall_count == before + 1

                config.expression.slow_threshold = 0.0
                tool, reply = await send('ordinary_slow', 'E2E 喜欢什么', False)
                assert len(tool) == 1
                assert not ({s['function']['name'] for s in tool[0]['tools']} & DISCUSSION)
                assert 'file_io' in {s['function']['name'] for s in tool[0]['tools']}
                assert '[讨论范式]' not in reply['messages'][0]['content']
                assert 'profile-marker' in reply['messages'][0]['content']
                assert '用户：E2E 我喜欢研究天文。' in reply['messages'][1]['content']

                for mode, expected_rounds in [('unauthorized', 4), ('failure', 2), ('round_limit', 4), ('duplicate', 4), ('item_limit', 4)]:
                    before = (await store.get_conversation_segment('segment-e2e')).recall_count
                    tool, reply = await send(mode, '嗯', True, mode)
                    assert len(tool) == expected_rounds
                    system = reply['messages'][0]['content']
                    if mode == 'unauthorized':
                        assert 'file_io:' not in system
                        assert not Path('workspace/must-not-exist.txt').exists()
                    if mode == 'failure':
                        assert '工具 memory_recall 执行失败' in system
                        assert (await store.get_conversation_segment('segment-e2e')).recall_count == before
                    if mode == 'duplicate':
                        assert system.count('memory_recall:') == 1
                        assert (await store.get_conversation_segment('segment-e2e')).recall_count == before + 1
                    if mode == 'item_limit':
                        assert system.count('deep_research:') == 5
                invalid = await client.post('/api/chat', json={'message': '嗯', 'discussion': 'true'})
                assert invalid.status_code == 422

                # Isolated memory replay through HTTP -> bus -> segment/facts -> final prompt.
                fake.memory_replay = True
                config.expression.slow_threshold = 99.0
                with patch.object(app.memory, '_embed', None):
                    _, source_reply = await send('memory_source',
                        '换个话题，我在北京，我喜欢米饭。生日那天你陪我走过了低谷。', False)
                    await asyncio.wait_for(app.bus._wait_until_idle(), 10)
                    await app.memory.finalize_conversation_segment()
                    original = await app.bus.get_event(source_reply['cid'])
                    original_at = datetime.fromtimestamp(original.timestamp, timezone.utc).isoformat(timespec='seconds')
                    assert original_at in source_reply['messages'][1]['content']
                    facts = await app.memory.search_facts('我在哪个城市')
                    assert [(f.predicate, f.object_value) for f in facts] == [('当前所在地', '北京')]
                    source_segment = await store.get_conversation_segment(facts[0].source_segment_id)
                    extraction = [c for c in fake.calls if c['type'] == 'fact_extraction'][-1]
                    assert original_at in extraction['messages'][1]['content']
                    for threshold, label in [(99.0, 'fast'), (0.0, 'slow')]:
                        config.expression.slow_threshold = threshold
                        for question, marker in [('我住哪儿', '北京'), ('我喜欢吃什么', '米饭'),
                                                 ('还记得生日那天吗', '生日那天你陪我走过了低谷')]:
                            # Remove the sliding window: this must be durable recall.
                            app.expression._history.clear()
                            before = (await store.get_conversation_segment(source_segment.id)).recall_count
                            _, recalled = await send(f'memory_{label}_{marker}', question, False)
                            await asyncio.wait_for(app.bus._wait_until_idle(), 10)
                            user = recalled['messages'][1]['content']
                            full = '\n'.join(m['content'] for m in recalled['messages'])
                            assert marker in full
                            if '生日' in question:
                                assert '[相关对话段' in user
                                assert original_at in user and '逐句时间=未知' in user
                            cid = recalled['cid']
                            current = await app.bus.get_event(cid)
                            current_at = datetime.fromtimestamp(current.timestamp, timezone.utc).isoformat(timespec='seconds')
                            assert f'消息时间={current_at}' in user
                            saved = await store.get_conversation_segment(source_segment.id)
                            admitted = source_segment.raw_content[:50] in user
                            assert saved.recall_count == before + int(admitted)
                            if admitted:
                                await app.memory.record_conversation_segment_recall(saved.id, cid)
                                assert (await store.get_conversation_segment(saved.id)).recall_count == saved.recall_count
                    app.expression._history.clear()
                    _, greeting = await send('memory_greeting', '早上好呀，尼克斯', False)
                    assert '[相关对话段' not in greeting['messages'][1]['content']
                    assert '[相关事实]' not in greeting['messages'][0]['content']
                    await asyncio.wait_for(app.bus._wait_until_idle(), 10)
                    attempt_id = await app.expression.register_question(
                        '你还好吗？', InteractionKind.CHAT_ASK, 'timeout-source', 'timeout-e2e')
                    await asyncio.wait_for(app.bus._wait_until_idle(), 10)
                    interactions = ExpressionInteractionStore(app.database)
                    attempt = await interactions.get(attempt_id)
                    before_memory = len(await app.memory.list_memories())
                    before_facts = len(await MemoryFactStore(app.database).recent())
                    await app.expression.check_timeouts(attempt.expires_at + 1)
                    await app.expression.check_timeouts(attempt.expires_at + 2)
                    assert (await interactions.get(attempt_id)).status is InteractionStatus.EXPIRED
                    assert len(await app.memory.list_memories()) == before_memory
                    assert len(await MemoryFactStore(app.database).recent()) == before_facts
                    print(json.dumps({'memory_timeout': 'PASS', 'new_memories': 0, 'new_facts': 0}), flush=True)
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
        print(json.dumps({'result': 'PASS', 'http_sse_scenarios': len(rows), 'strict_boolean_validation': 'PASS',
                          'limitations': 'Scripted LLM and embedding; no real model quality or external network validation.'}, ensure_ascii=False), flush=True)
    finally:
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(server_task, 10)
        await app.memory.finalize_conversation_segment()
        await app.bus.close()
        await asyncio.wait_for(bus_task, 10)

asyncio.run(main())
