"""0.2.5: the room for history, a summary that stays put between turns, and one retry when the input is too long."""
import json

import pytest

from context_budget import clip, count, overflowed, policy
from summaries import PUBLIC_SECTIONS
from test_visibility_chat import env, add   # noqa: F401  (fixture)

CLI = {'model': 'm', 'context': {'model': 'm', 'window_tokens': 400000}}


def test_estimate_counts_chinese_and_ascii():
    assert count('研' * 1000) == 1300
    assert count('a' * 3500) == 1000
    assert count('') == 0
    text = '空间导航 spatial navigation ' * 50
    assert count(clip(text, 100)) <= 100 < count(clip(text, 100) + text[len(clip(text, 100))])


def test_cli_keeps_no_tool_reserve_but_api_does():
    cli = policy(CLI, True, '256k')
    assert (cli['tool_reserve_tokens'], cli['output_reserve_tokens'], cli['initial_budget_tokens']) == (0, 32000, 262144)
    # The case reported on 2026-11-25: a 400K subscription CLI with tools used to get 31,488.
    assert policy(CLI, True, '1m')['initial_budget_tokens'] == int((400000 - 32000 - 32000) * .85)
    api = dict(CLI, type='api', max_output_tokens=32000, context=dict(CLI['context'], backend_reserve=4096))
    assert policy(api, True, '1m')['tool_reserve_tokens'] == 208512
    assert policy(api, False, '1m')['tool_reserve_tokens'] == 0


@pytest.mark.parametrize('message', ['claude CLI exit 1; CLI said: Prompt is too long', 'API HTTP status 400; the provider said the input is too long',
                                     "This model's maximum context length is 128000 tokens", 'context_length_exceeded', 'Input exceeds the context window'])
def test_overflow_messages(message):
    assert overflowed(message)


def test_other_failures_are_not_overflow():
    assert not overflowed('API HTTP status 502') and not overflowed('Failed to authenticate') and not overflowed(None)


def run(r, q):
    return r.tasks.wait(r.tasks.start('general', 'claude', q)['task_id'])


def test_long_chinese_round_fits_whole(env):
    r, s, _, _ = env
    r.tasks.config['mock'] = dict(CLI)
    for i in range(8): add(s, f'第 {i} 条：' + '老年人空间导航与视觉流的关系。' * 800)   # about 100K estimated tokens in all
    t = run(r, add(s, '继续'))
    assert t['state'] == 'completed' and t['trimmed_history_ranges'] == [] and len(t['history_included_ids']) == 8


def summary(sums, s, ids):
    return sums.revision('general', {k: f'#{ids[-1]} point' for k in PUBLIC_SECTIONS}, ids, s.get_by_id, author='automatic', automatic=True)


def test_summary_stays_put_until_history_no_longer_fits(env):
    r, s, sums, _ = env
    r.tasks.config['mock'] = dict(CLI)
    rows = [add(s, f'message {i} ' + 'evidence ' * 40) for i in range(6)]
    first = summary(sums, s, [rows[0]['id'], rows[1]['id']])
    t1 = run(r, add(s, 'first question'))
    assert t1['snapshot_summary_version'] == first['version']
    prompt1 = (r.tasks.directory / t1['task_id'] / 'input.json').read_text('utf-8')
    newer = summary(sums, s, [m['id'] for m in rows[:5]])
    t2 = run(r, add(s, 'second question'))
    # Still the summary this model read last time: the start of its input is the same as before.
    assert t2['snapshot_summary_version'] == first['version']
    prompt2 = (r.tasks.directory / t2['task_id'] / 'input.json').read_text('utf-8')
    start = prompt1.index('"history"')
    assert prompt2[:start] == prompt1[:start] and json.loads(prompt2)['history'][0]['id'] == rows[2]['id']
    # A small room: the history after the old summary no longer fits, so the newer one is taken.
    r.tasks.config['mock'] = dict(CLI, context=dict(CLI['context'], window_tokens=66000))
    for i in range(6): add(s, 'later ' + 'detail ' * 120)
    t3 = run(r, add(s, 'third question'))
    assert t3['snapshot_summary_version'] == newer['version']
    assert json.loads(r.builder.summary_pins_path.read_text('utf-8'))['general']['claude']['summary'] == newer['version']


def test_confirmed_summary_replaces_the_pinned_one(env):
    r, s, sums, _ = env
    r.tasks.config['mock'] = dict(CLI)
    rows = [add(s, f'message {i}') for i in range(4)]
    summary(sums, s, [rows[0]['id']])
    run(r, add(s, 'q1'))
    confirmed = sums.revision('general', {k: f'#{rows[2]["id"]} agreed' for k in PUBLIC_SECTIONS}, [m['id'] for m in rows[:3]], s.get_by_id, confirm=True)
    assert run(r, add(s, 'q2'))['snapshot_summary_version'] == confirmed['version']


def test_input_too_long_is_retried_once_with_less_history(env):
    r, s, _, _ = env
    r.tasks.config['mock'] = dict(CLI)
    for i in range(6): add(s, 'old ' + 'words ' * 20000)   # about 34K estimated tokens each
    mock, seen = r.tasks.backends['mock'], []
    real = mock.run
    def refuse_first(prompt, task, attach, cancel):
        seen.append(len(json.loads(prompt)['history']))
        if len(seen) == 1: raise RuntimeError('claude CLI exit 1; no reply committed; CLI said: Prompt is too long')
        return real(prompt, task, attach, cancel)
    mock.run = refuse_first
    try: t = run(r, add(s, 'q'))
    finally: mock.run = real
    assert t['state'] == 'completed' and t['context_retry']['budget'] == int(262144 * .6)
    assert seen[0] == 6 and seen[1] < 6 and t['trimmed_history_ranges']


def test_cli_runs_in_one_folder_per_participant_and_chat(tmp_path):
    from chat_backends import CLIBackend
    task_dir = tmp_path / 'chat_calls' / 'task-1'; task_dir.mkdir(parents=True)
    a = CLIBackend.working_folder(task_dir, dict(room_id='room-1', identity='claude-fable-5-2'))
    b = CLIBackend.working_folder(tmp_path / 'chat_calls' / 'task-2', dict(room_id='room-1', identity='claude-fable-5-2'))
    c = CLIBackend.working_folder(task_dir, dict(room_id='room-1', identity='codex'))
    assert a == b != c and a.is_dir() and a.parent.parent.name == '_cwd'
    assert CLIBackend.working_folder(task_dir, {}) == task_dir


def test_openai_cache_key_only_for_openai_itself():
    import api_protocols as wire
    task = dict(room_id='r1', identity='gpt-6-astra')
    official = dict(protocol='openai-responses', base_url='https://api.openai.com/v1')
    key = wire.cache_key(official, task)
    assert key.startswith('chattera-') and len(key) == 41 and 'r1' not in key
    assert wire.cache_key(official, task) == key != wire.cache_key(official, dict(task, identity='gpt-6-1-sol'))
    assert wire.cache_key(dict(protocol='openai-chat', base_url='https://apinebula.ai/v1'), task) is None
    assert wire.cache_key(dict(protocol='anthropic-messages', base_url='https://api.openai.com/v1'), task) is None


# ---- the fixed part of the input, sent as its own cached block ----
def parts_of(r, t):
    from context_budget import prompt_parts
    folder = r.tasks.directory / t['task_id']
    return prompt_parts((folder / 'input.json').read_text('utf-8'), json.loads((folder / 'snapshot.json').read_text('utf-8')))


def test_fixed_part_stays_the_same_between_turns(env):
    r, s, _, _ = env
    r.tasks.config['mock'] = dict(CLI)
    for i in range(10): add(s, f'note {i} ' + 'observation ' * 60)
    first = parts_of(r, run(r, add(s, 'q1')))
    for i in range(3): add(s, f'later {i}')
    second = parts_of(r, run(r, add(s, 'q2')))
    assert first and second and second[0] == first[0]            # byte for byte: the provider's cache matches it
    assert '"q2"' in second[1] and '"later 2"' in second[1] and '"q1"' in second[1]
    json.loads(second[0] + second[1])                             # the two parts are still one valid input
    # Once the new part grows past a quarter of the fixed part, the fixed part moves forward.
    for i in range(4): add(s, f'much later {i} ' + 'detail ' * 4000)   # about 32K estimated tokens: past the 20K floor
    third = parts_of(r, run(r, add(s, 'q3')))
    assert third[0] != first[0] and third[0].startswith(first[0]) and '"q3"' in third[1]


def test_claude_cli_sends_the_fixed_part_with_a_one_hour_marker():
    import cli_adapters
    adapter, prompt = cli_adapters.get('claude'), '{"history":[{"id":1,"text":"a"}],"current":{"id":2}}'
    import hashlib
    snapshot = dict(prompt_split=dict(chars=32, sha256=hashlib.sha256(prompt[:32].encode()).hexdigest()))
    command, data = adapter.prepare_input({}, ['claude', '-p', '--output-format', 'x'], prompt, json.loads(prompt), snapshot)
    content = json.loads(data)['message']['content']
    assert '--input-format' in command and [b['text'] for b in content] == [prompt[:32], prompt[32:]]
    assert content[0]['cache_control'] == {'type': 'ephemeral', 'ttl': '1h'} and 'cache_control' not in content[1]
    # Changed since it was built (for example redacted): sent as one text, as before.
    assert adapter.prepare_input({}, ['claude'], prompt.replace('a', 'b'), json.loads(prompt), snapshot)[1] == prompt.replace('a', 'b').encode()


def test_anthropic_api_marks_the_fixed_part():
    import api_protocols as wire
    cfg = dict(protocol='anthropic-messages', model='m', max_output_tokens=10, _split=True)
    body = wire.body(cfg, wire.initial(cfg, 'ab', [], ('fixed', 'new')), [])
    blocks = body['messages'][0]['content']
    assert [b['text'] for b in blocks] == ['fixed', 'new'] and all(b.get('cache_control') == {'type': 'ephemeral'} for b in blocks)
    assert json.dumps(body).count('cache_control') == 3            # system, fixed part, end of the request


def test_cli_retries_as_one_block_when_the_marker_is_refused(tmp_path):
    from chat_backends import CLIBackend
    backend, calls = CLIBackend('claude', {'executable': 'x'}), []
    def fake(prompt, task, attach, cancel, split=True):
        calls.append(split); task['_split_used'] = split
        if split: raise RuntimeError('claude CLI exit 1; no reply committed; CLI said: API Error: 400 messages.1.content.0.cache_control.ttl')
        return {'text': 'ok'}
    backend._run = fake
    task = {}
    assert backend.run('p', task, None, None) == {'text': 'ok'} and calls == [True, False] and 'cache_control' in task['split_refused']
