"""Per-provider request settings exposed in Settings → Compatibility (Anthropic workspace ID, OpenAI output-limit name)."""
import json

import api_protocols as wire
import chat_registry


def test_anthropic_workspace_header_and_setting():
    cfg = dict(protocol='anthropic-messages', anthropic_workspace='wrkspc_01ABC')
    assert wire.headers(cfg, 'sk-test')['anthropic-workspace-id'] == 'wrkspc_01ABC'
    assert 'anthropic-workspace-id' not in wire.headers(dict(protocol='anthropic-messages'), 'sk-test')
    assert 'anthropic_workspace' in chat_registry.PROFILE_FIELDS and 'output_limit_field' in chat_registry.PROFILE_FIELDS


def test_error_hint_names_the_workspace_header_but_no_other_text():
    body = json.dumps({'type': 'error', 'error': {'type': 'invalid_request_error', 'message':
        'This API key is not scoped to a workspace, so this request must include the anthropic-workspace-id header with the ID of the workspace to use.'}})
    assert wire.error_hint(body) == '; provider-referenced parameters: anthropic-workspace-id'
    assert wire.error_hint(json.dumps({'error': {'message': 'Unsupported parameter: max_tokens; use max_completion_tokens'}})) \
        == '; provider-referenced parameters: max_tokens, max_completion_tokens'


def test_openai_output_limit_name():
    cfg = dict(protocol='openai-chat', model='m', max_output_tokens=100)
    assert wire.body(cfg, [], None)['max_tokens'] == 100
    newer = wire.body(dict(cfg, output_limit_field='max_completion_tokens'), [], None)
    assert newer['max_completion_tokens'] == 100 and 'max_tokens' not in newer
