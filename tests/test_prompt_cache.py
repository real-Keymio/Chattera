"""Anthropic prompt caching: markers in the request, fallback for endpoints without caching, hit rate per protocol."""
import asyncio
import copy
import json
import threading

import httpx
import pytest

import api_protocols as wire
import chat_api
import chat_cache
from chat_api import APIBackend

SCHEMA = [{'type': 'function', 'function': {'name': 'f', 'description': 'd', 'parameters': {'type': 'object', 'properties': {}}}}]


def anthropic(**extra):
    return dict(dict(protocol='anthropic-messages', model='m', max_output_tokens=100), **extra)


def test_anthropic_request_marks_system_context_and_tail():
    cfg = anthropic()
    messages = wire.initial(cfg, 'the whole chat context', [])
    wire.tool_results(cfg, messages, [{'type': 'thinking', 'thinking': '', 'signature': 's'}, {'type': 'tool_use', 'id': 't1', 'name': 'f', 'input': {}}],
                      [{'id': 't1'}], ['result'])
    stored = copy.deepcopy(messages)
    body = wire.body(cfg, messages, SCHEMA)
    assert body['system'] == [{'type': 'text', 'text': wire.SYSTEM, 'cache_control': {'type': 'ephemeral'}}]
    assert body['messages'][0]['content'][-1]['cache_control'] == {'type': 'ephemeral'}    # end of Chattera's context
    assert body['messages'][-1]['content'][-1]['cache_control'] == {'type': 'ephemeral'}   # latest tool results
    assert 'cache_control' not in json.dumps(body['messages'][1])                         # thinking / tool_use untouched
    assert json.dumps(body).count('cache_control') == 3 <= 4
    assert messages == stored                                                             # the conversation itself is not changed
    # Same content as without caching, only the markers differ.
    plain = wire.body(anthropic(anthropic_cache='off'), messages, SCHEMA)
    assert 'cache_control' not in json.dumps(plain) and plain['system'] == wire.SYSTEM
    strip = lambda value: json.loads(json.dumps(value).replace(', "cache_control": {"type": "ephemeral"}', ''))
    assert strip(body['messages']) == plain['messages'] and body['system'][0]['text'] == plain['system']


def test_one_hour_lifetime_on_every_marker():
    body = wire.body(anthropic(anthropic_cache='1h'), wire.initial(anthropic(), 'context', []), [])
    markers = [b['cache_control'] for b in body['system']] + [b['cache_control'] for m in body['messages'] for b in m['content'] if 'cache_control' in b]
    assert markers == [{'type': 'ephemeral', 'ttl': '1h'}] * 2


@pytest.mark.parametrize('protocol', ['openai-chat', 'openai-responses', 'google-generate-content'])
def test_other_protocols_send_no_cache_fields(protocol):
    cfg = dict(protocol=protocol, model='m', max_output_tokens=100, anthropic_cache='1h')
    body = wire.body(cfg, wire.initial(cfg, 'context', []), SCHEMA)
    assert 'cache_control' not in json.dumps(body)


def test_endpoint_without_caching_falls_back_once_and_remembers(tmp_path, monkeypatch):
    seen = []
    def handler(request):
        body = json.loads(request.content); seen.append('cache_control' in json.dumps(body))
        if 'cache_control' in json.dumps(body):
            return httpx.Response(400, json={'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'Extra inputs are not permitted: cache_control'}})
        return httpx.Response(200, json={'model': 'm', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'ok'}],
                                         'usage': {'input_tokens': 10, 'output_tokens': 2}})
    real = httpx.AsyncClient
    monkeypatch.setattr(chat_api.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    (tmp_path / 'snapshot.json').write_text(json.dumps({'history': [], 'research_allowed': False}), 'utf-8')
    cfg = dict(type='api', protocol='anthropic-messages', identity='x', enabled=True, mock=True, base_url='http://127.0.0.1:1/v1', model='m',
               max_requests=None, timeout=60, max_output_tokens=100, max_requests_per_task=3)
    backend = APIBackend('relay', cfg, tmp_path)
    run = lambda: asyncio.run(backend.run_async(json.dumps({'current': {'text': 'q'}}), {'directory': str(tmp_path)}, threading.Event()))
    assert run()['text'] == 'ok' and seen == [True, False] and backend.cache_unsupported
    assert run()['text'] == 'ok' and seen == [True, False, False]       # remembered: no second failure


def test_cache_rejection_is_recognised_only_for_cache_errors():
    assert wire.cache_rejected(b'{"error":{"message":"cache_control is not supported"}}')
    assert wire.cache_rejected('unknown field ttl')
    assert not wire.cache_rejected(b'{"error":{"message":"max_tokens: too large"}}')


# ---- hit rate: each provider counts cached input its own way ----
def test_anthropic_input_tokens_exclude_the_cache():
    usage = {'input_tokens': 100, 'cache_creation_input_tokens': 300, 'cache_read_input_tokens': 600, 'output_tokens': 5}
    assert chat_cache.one('anthropic-messages', usage) == (1000, 600, 300)
    s = chat_cache.summarize('anthropic-messages', [usage, {'input_tokens': 50, 'cache_creation_input_tokens': 0, 'cache_read_input_tokens': 950}])
    assert (s['input_tokens'], s['cached_tokens'], s['written_tokens'], s['rate']) == (2000, 1550, 300, 77.5)


def test_openai_chat_cached_tokens_are_part_of_the_input():
    assert chat_cache.one('openai-chat', {'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': 768}}) == (1000, 768, None)
    assert chat_cache.one('openai-chat', {'prompt_tokens': 1000, 'prompt_cache_hit_tokens': 900, 'prompt_cache_miss_tokens': 100}) == (1000, 900, None)  # DeepSeek


def test_openai_responses_and_google_and_codex():
    assert chat_cache.one('openai-responses', {'input_tokens': 2000, 'input_tokens_details': {'cached_tokens': 1024}}) == (2000, 1024, None)
    assert chat_cache.one('google-generate-content', {'promptTokenCount': 4000, 'cachedContentTokenCount': 3000}) == (4000, 3000, None)
    assert chat_cache.one('codex-cli', {'input_tokens': 5000, 'cached_input_tokens': 4500}) == (5000, 4500, None)
    s = chat_cache.summarize('claude-cli', [{'input_tokens': 10, 'cache_read_input_tokens': 90, 'cache_creation_input_tokens': 0}], subscription=True)
    assert s['rate'] == 90.0 and s['subscription'] is True


def test_no_cache_information_is_unknown_not_zero():
    for kind, usage in (('openai-chat', {'prompt_tokens': 1000}), ('google-generate-content', {'promptTokenCount': 10}),
                        ('anthropic-messages', {'input_tokens': 10}), ('openai-responses', {'input_tokens': 10}), ('unknown', {})):
        assert chat_cache.one(kind, usage) is None
        assert chat_cache.summarize(kind, [usage])['rate'] is None
    assert chat_cache.one('openai-chat', {'prompt_tokens': 1000, 'prompt_tokens_details': {'cached_tokens': 0}}) == (1000, 0, None)  # a real 0 %


def test_recent_hit_rate_of_a_connection():
    tasks = [dict(backend='a', state='completed', cache=dict(input_tokens=1000, cached_tokens=800)),
             dict(backend='a', state='completed', cache=dict(input_tokens=3000, cached_tokens=1200)),
             dict(backend='a', state='failed', cache=dict(input_tokens=9, cached_tokens=0)),
             dict(backend='b', state='completed', cache=dict(input_tokens=None, cached_tokens=None))]
    assert chat_cache.recent(tasks, 'a') == dict(replies=2, rate=50.0, subscription=False)
    assert chat_cache.recent(tasks, 'b') is None
