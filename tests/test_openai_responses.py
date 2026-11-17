"""OpenAI Responses API: newer OpenAI reasoning models refuse function tools with reasoning_effort on /chat/completions."""
import json

import pytest

import api_protocols as wire
import chat_search
from model_settings import validate_settings

CFG = dict(type='api', protocol='openai-responses', base_url='https://api.openai.com/v1', model='gpt-x', max_output_tokens=500)
SCHEMA = [{'type': 'function', 'function': {'name': 'web_search', 'description': 'search', 'parameters': {'type': 'object', 'properties': {}}}}]


def test_request_shape():
    assert wire.endpoint(CFG) == 'https://api.openai.com/v1/responses'
    assert wire.headers(CFG, 'sk-x') == {'Authorization': 'Bearer sk-x'}
    image = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AAAA'}}
    messages = wire.initial(CFG, 'hi', [image])
    assert messages == [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'hi'}, {'type': 'input_image', 'image_url': 'data:image/png;base64,AAAA'}]}]
    body = wire.body(dict(CFG, effort='high'), messages, SCHEMA)
    assert body['reasoning'] == {'effort': 'high'} and body['max_output_tokens'] == 500 and body['store'] is False
    assert body['include'] == ['reasoning.encrypted_content'] and body['instructions'] == wire.SYSTEM
    assert body['tools'] == [{'type': 'function', 'name': 'web_search', 'description': 'search', 'parameters': {'type': 'object', 'properties': {}}, 'strict': False}]
    assert wire.body(CFG, messages, SCHEMA, 'web_search_tool')['tools'] == [{'type': 'web_search'}]
    assert 'reasoning' not in wire.body(CFG, messages, [])


def test_tool_round_sends_every_output_item_back():
    reasoning = {'type': 'reasoning', 'id': 'rs_1', 'summary': [], 'encrypted_content': 'opaque'}
    call = {'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1', 'name': 'web_search', 'arguments': '{"query": "x"}', 'status': 'completed'}
    parsed = wire.consume(CFG, {'status': 'completed', 'model': 'gpt-x-2026', 'output': [reasoning, call], 'usage': {'input_tokens': 10, 'output_tokens': 5}})
    assert parsed['calls'] == [{'id': 'call_1', 'name': 'web_search', 'args': {'query': 'x'}}]
    assert parsed['counts'] == {'prompt_tokens': 10, 'completion_tokens': 5} and parsed['model'] == 'gpt-x-2026'
    messages = wire.initial(CFG, 'hi', [])
    wire.tool_results(CFG, messages, parsed['state'], parsed['calls'], ['result'])
    assert messages[1:] == [reasoning, call, {'type': 'function_call_output', 'call_id': 'call_1', 'output': 'result'}]
    wire.attach_images(CFG, messages, [{'media_type': 'image/png', 'data': 'AAAA'}])
    assert messages[-1]['content'][1] == {'type': 'input_image', 'image_url': 'data:image/png;base64,AAAA'}


def test_final_answer_and_incomplete_status():
    message = {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Hello', 'annotations': []}]}
    assert wire.consume(CFG, {'status': 'completed', 'model': 'm', 'output': [message], 'usage': {}})['text'] == 'Hello'
    with pytest.raises(ValueError):
        wire.consume(CFG, {'status': 'incomplete', 'model': 'm', 'output': [message], 'usage': {}})


def test_official_search_record():
    items = [{'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'search', 'query': 'chattera', 'sources': [{'type': 'url', 'url': 'https://a.example/'}]}},
             {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'open_page', 'url': 'https://a.example/page'}},
             {'type': 'message', 'content': [{'type': 'output_text', 'text': 'x', 'annotations': [
                 {'type': 'url_citation', 'url': 'https://a.example/page', 'title': 'A', 'start_index': 0, 'end_index': 1}]}]}]
    entries, citations = chat_search.from_responses(items, 'provider web_search tool')
    assert [e['kind'] for e in entries] == ['search', 'read'] and entries[0]['queries'] == ['chattera']
    assert entries[0]['results'][0]['url'] == 'https://a.example/' and citations[0]['url'] == 'https://a.example/page'
    assert chat_search.native_kind('openai', CFG) == 'web_search_tool'
    assert chat_search.native_kind('relay', dict(CFG, base_url='https://relay.example/v1')) is None


def test_effort_needs_no_extra_mapping_and_thinking_is_refused():
    assert validate_settings(CFG, {'effort': 'high'}, 'openai') == {'effort': 'high'}
    with pytest.raises(ValueError):
        validate_settings(CFG, {'thinking': {'type': 'enabled'}}, 'openai')


def test_hint_points_to_responses():
    body = json.dumps({'error': {'message': "Function tools with reasoning_effort are not supported for gpt-x in /v1/chat/completions. "
                                            "To use function tools, use /v1/responses or set reasoning_effort to 'none'.", 'param': 'reasoning_effort'}})
    assert wire.error_hint(body) == '; provider-referenced parameters: tools, reasoning_effort, /v1/responses'
