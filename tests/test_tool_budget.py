"""A model that keeps calling tools still ends with an answer: when the budget or the rounds run out, it gets one last
request with tool calls switched off and is told to answer from what it has."""
import asyncio
import json
import threading

import httpx
import pytest

import api_protocols as wire
import chat_api
from chat_api import APIBackend
from chat_tools import TOOL_BUDGET


def config(**extra):
    return dict(dict(type='api', protocol='openai-chat', identity='x', enabled=True, mock=True, base_url='http://127.0.0.1:1/v1', model='m',
                     max_requests=None, timeout=60, max_output_tokens=1024, max_requests_per_task=10, tools=True), **extra)


def run(tmp_path, monkeypatch, handler, **extra):
    (tmp_path / 'snapshot.json').write_text(json.dumps({'history': [{'id': 1, 'sender': 'user', 'text': 'q'}], 'research_allowed': False}), 'utf-8')
    real = httpx.AsyncClient
    monkeypatch.setattr(chat_api.httpx, 'AsyncClient', lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    backend = APIBackend('x', config(**extra), tmp_path)
    return asyncio.run(backend.run_async(json.dumps({'current': {'text': 'q'}}), {'directory': str(tmp_path)}, threading.Event()))


def greedy(seen):
    """Six parallel searches every round, until it is told it may not call tools any more."""
    def handler(request):
        body = json.loads(request.content); seen.append(body)
        if body.get('tool_choice') == 'none':
            return httpx.Response(200, json={'model': 'm', 'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'final answer'}}]})
        calls = [{'id': f'c{len(seen)}-{i}', 'type': 'function', 'function': {'name': 'chat_search', 'arguments': json.dumps({'query': f'q{len(seen)}-{i}'})}} for i in range(6)]
        return httpx.Response(200, json={'model': 'm', 'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None, 'tool_calls': calls}}]})
    return handler


def test_spent_budget_ends_with_an_answer(tmp_path, monkeypatch):
    monkeypatch.setattr('chat_tools.TOOL_BUDGET', 24)
    seen = []
    result = run(tmp_path, monkeypatch, greedy(seen))
    assert result['text'] == 'final answer'
    assert len(result['tool_calls']) == 24  # 4 rounds of 6 parallel calls, all run
    assert seen[-1]['tool_choice'] == 'none' and 'tools' in seen[-1]
    assert 'Tool budget for this reply is used up' in seen[-1]['messages'][-1]['content']
    assert all('tool_choice' not in body for body in seen[:-1])


def test_last_round_ends_with_an_answer(tmp_path, monkeypatch):
    """Budget not spent, but the request rounds are: the last request still asks for the answer."""
    monkeypatch.setattr('chat_tools.TOOL_BUDGET', 1000)
    seen = []
    result = run(tmp_path, monkeypatch, greedy(seen), max_requests_per_task=3)
    assert result['text'] == 'final answer' and len(seen) == 4 and seen[-1]['tool_choice'] == 'none'


def test_going_round_in_circles_ends_with_an_answer(tmp_path, monkeypatch):
    """The same call with the same arguments, again and again: answered from the first result, then the model is asked to finish."""
    seen = []
    def handler(request):
        body = json.loads(request.content); seen.append(body)
        if body.get('tool_choice') == 'none':
            return httpx.Response(200, json={'model': 'm', 'choices': [{'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'done'}}]})
        call = {'id': f'c{len(seen)}', 'type': 'function', 'function': {'name': 'chat_search', 'arguments': json.dumps({'query': 'same'})}}
        return httpx.Response(200, json={'model': 'm', 'choices': [{'finish_reason': 'tool_calls', 'message': {'role': 'assistant', 'content': None, 'tool_calls': [call]}}]})
    result = run(tmp_path, monkeypatch, handler)
    assert result['text'] == 'done' and len(seen) == 7   # 2 real calls, 4 refused repeats, then the final request


@pytest.mark.parametrize('protocol,check', [
    ('openai-responses', lambda b: b['tool_choice'] == 'none'),
    ('anthropic-messages', lambda b: b['tool_choice'] == {'type': 'none'}),
    ('google-generate-content', lambda b: b['toolConfig'] == {'functionCallingConfig': {'mode': 'NONE'}})])
def test_final_request_switches_tools_off_in_every_protocol(protocol, check):
    cfg = dict(protocol=protocol, model='m', max_output_tokens=100)
    schema = [{'type': 'function', 'function': {'name': 'f', 'description': 'd', 'parameters': {'type': 'object', 'properties': {}}}}]
    messages = wire.initial(cfg, 'q', [])
    assert check(wire.body(cfg, messages, schema, final=True))
    assert 'tool_choice' not in wire.body(cfg, messages, schema) and 'toolConfig' not in wire.body(cfg, messages, schema)
    wire.final_note(cfg, messages, 'answer now')
    assert 'answer now' in json.dumps(messages)


@pytest.mark.parametrize('protocol,value', [
    ('openai-chat', {'model': 'm', 'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': 'long text'}}]}),
    ('openai-responses', {'model': 'm', 'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
                          'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': 'long text'}]}]}),
    ('anthropic-messages', {'model': 'm', 'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': 'long text'}]}),
    ('google-generate-content', {'modelVersion': 'm', 'candidates': [{'finishReason': 'MAX_TOKENS', 'content': {'role': 'model', 'parts': [{'text': 'long text'}]}}]})])
def test_answer_cut_at_the_output_limit_is_kept(protocol, value):
    parsed = wire.consume(dict(protocol=protocol), value)
    assert parsed['truncated'] and parsed['text'] == 'long text'


def test_cut_answer_is_returned_with_a_note(tmp_path, monkeypatch):
    def handler(request):
        return httpx.Response(200, json={'model': 'm', 'choices': [{'finish_reason': 'length', 'message': {'role': 'assistant', 'content': 'partial answer'}}]})
    result = run(tmp_path, monkeypatch, handler)
    assert result['truncated'] and result['text'].startswith('partial answer') and '截断' in result['text']


def test_caches_follow_the_library_folder(tmp_path):
    from types import SimpleNamespace
    from chat_runtime import ChatRuntime
    runtime = SimpleNamespace(registry=SimpleNamespace(data={'library': {'root': str(tmp_path / 'cloud')}}), data_dir=tmp_path / 'data',
                              attachments=SimpleNamespace(cache=None), evidence=SimpleNamespace(cache=None))
    assert ChatRuntime.place_caches(runtime) is None   # folder missing (cloud drive not mounted): local
    assert runtime.attachments.cache == tmp_path / 'data' / 'attachment-cache'
    (tmp_path / 'cloud').mkdir()
    assert ChatRuntime.place_caches(runtime) == tmp_path / 'cloud' / 'cache'
    assert runtime.attachments.cache == tmp_path / 'cloud' / 'cache' / 'attachments' and runtime.evidence.cache == tmp_path / 'cloud' / 'cache' / 'web'
