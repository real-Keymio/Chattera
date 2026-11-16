"""Independent web search: the path is chosen from what a connection declares, and the record comes only from tool events."""
import api_protocols as wire
from chat_search import CliTrace, from_google, from_openai, from_tool_events, instruction, native_kind, plan, summary

API = dict(type='api', protocol='openai-chat', base_url='https://example.test/v1')


def test_path_follows_the_connection_not_the_model_name():
    assert native_kind('claude', {}) == 'claude' and native_kind('codex', {}) == 'codex'
    assert native_kind('claude', {'native_search': 'off'}) is None
    assert native_kind('x', dict(API, model='deepseek-flash')) is None                      # a name proves nothing
    assert native_kind('x', dict(API, base_url='https://api.deepseek.com')) == 'deepseek'
    assert native_kind('x', dict(API, base_url='https://api.deepseek.com', native_search='off')) is None
    assert native_kind('x', dict(API, native_search='web_search_tool')) == 'web_search_tool'
    assert native_kind('x', dict(API, protocol='google-generate-content')) == 'google'
    assert plan('x', API, True, True, 'independent')['path'] == 'multichat-tools'
    assert plan('x', API, False, True, 'independent')['path'] == 'multichat-planned'
    private = plan('claude', {}, True, False, 'independent')
    assert private['path'] == 'none' and 'private' in private['reason']                      # nothing leaves a private context
    assert 'not available' in instruction(private, False)['web_search']
    assert 'shared_evidence' in instruction(plan('claude', {}, True, True, 'hybrid'), True)['web_search']


def test_request_bodies_carry_the_official_tool_only():
    schemas = [{'type': 'function', 'function': {'name': 'calc', 'description': '', 'parameters': {'type': 'object', 'properties': {}}}}]
    cfg = dict(API, model='m', max_output_tokens=100)
    assert wire.body(cfg, [], schemas)['tools'] == schemas
    assert wire.body(cfg, [], schemas, 'deepseek')['tools'] == [{'type': 'deepseek_web_search'}, {'type': 'deepseek_read_url'}]
    assert wire.body(cfg, [], schemas, 'web_search_tool')['tools'] == [{'type': 'web_search'}]
    google = dict(type='api', protocol='google-generate-content', base_url='https://example.test/v1beta', model='g', max_output_tokens=100)
    assert wire.body(google, [], schemas, 'google')['tools'] == [{'google_search': {}}]
    assert wire.headers(dict(google, google_auth='bearer'), 'k') == {'Authorization': 'Bearer k'} and wire.headers(google, 'k') == {'x-goog-api-key': 'k'}


def test_records_from_provider_responses():
    message = {'content': 'Rust 1.100.0 is out.', 'tool_calls': None, 'annotations': [{'type': 'url_citation', 'url_citation': {'url': 'https://a.test/x', 'title': 'A', 'start_index': 0, 'end_index': 4}}],
               'server_tool_calls': [{'calls': [{'type': 'deepseek_web_search', 'status': 'completed', 'action': {'type': 'search', 'queries': ['rust stable']},
                                                 'sources': [{'type': 'url', 'url': 'https://a.test/x', 'title': 'A', 'snippet': 's', 'site_name': 'a'}, {'url': 'javascript:1'}]}]}]}
    parsed = wire.consume(dict(API, model='m'), {'choices': [{'message': message, 'finish_reason': 'stop'}], 'model': 'm', 'usage': {}}, 'deepseek')
    assert parsed['search'][0]['queries'] == ['rust stable'] and [r['url'] for r in parsed['search'][0]['results']] == ['https://a.test/x']
    assert parsed['citations'][0]['end'] == 4
    only_notes, cited, _ = from_openai({'annotations': [{'type': 'url_citation', 'url': 'https://b.test/', 'title': 'B', 'publish_time': '2026-11-01'}]}, {}, 'provider web_search tool')
    assert only_notes[0]['results'][0]['published'] == '2026-11-01' and 'not the queries' in only_notes[0]['note'] and cited
    entries, cites = from_google({'groundingMetadata': {'webSearchQueries': ['q'], 'groundingChunks': [{'web': {'uri': 'https://r.test/1', 'title': 'site.org'}}],
                                                        'groundingSupports': [{'segment': {'startIndex': 3, 'endIndex': 9}, 'groundingChunkIndices': [0, 7]}]}})
    assert entries[0]['queries'] == ['q'] and cites == [dict(url='https://r.test/1', title='site.org', start=3, end=9)]
    assert from_google({}) == ([], [])


def test_records_from_cli_streams_and_multichat_tools():
    seen = []
    claude = CliTrace('claude', seen.append)
    claude.feed({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'WebSearch', 'input': {'query': 'rust'}},
                                                              {'type': 'tool_use', 'id': 't2', 'name': 'WebFetch', 'input': {'url': 'https://a.test/'}},
                                                              {'type': 'tool_use', 'id': 't3', 'name': 'mcp__room__calc', 'input': {}}]}})
    claude.feed({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'text'}]},
                 'tool_use_result': {'query': 'rust', 'results': [{'content': [{'title': 'T', 'url': 'https://a.test/'}]}, 'summary text']}})
    claude.feed({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': 't2', 'is_error': True, 'content': 'blocked'}]}})
    assert [e['kind'] for e in claude.entries] == ['search', 'read'] and claude.entries[0]['results'][0]['title'] == 'T' and claude.entries[1]['error'] == 'blocked'
    assert len(seen) == 2
    codex = CliTrace('codex')
    codex.feed({'type': 'item.started', 'item': {'type': 'web_search', 'query': '', 'action': {'type': 'other'}}})
    codex.feed({'type': 'item.completed', 'item': {'type': 'web_search', 'query': 'a ...', 'action': {'type': 'search', 'queries': ['a', 'b']}}})
    codex.feed({'type': 'item.completed', 'item': {'type': 'web_search', 'action': {'type': 'open_page', 'url': 'https://c.test/'}}})
    assert codex.entries[0]['queries'] == ['a', 'b'] and codex.entries[1]['url'] == 'https://c.test/'
    own = from_tool_events([{'tool': 'web_search', 'status': 'success', 'time': 1.0, 'args': {'query': 'q'}, 'summary': {'query': 'q', 'provider': 'duckduckgo', 'results': [{'title': 'X', 'url': 'https://x.test/'}]}},
                            {'tool': 'web_read', 'status': 'failed', 'time': 2.0, 'args': {'url': 'https://y.test/'}, 'error_type': 'ValueError', 'error': 'no'},
                            {'tool': 'calc', 'status': 'success'}])
    assert [e['kind'] for e in own] == ['search', 'read'] and own[1]['error'].startswith('ValueError')
    assert summary(claude.entries + codex.entries + own) == dict(searches=3, pages=3, sources=4, errors=2)
