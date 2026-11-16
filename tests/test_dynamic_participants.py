"""Participants beyond the four built-in identities: own id, own connection, same audience and history rules."""
import json
import pytest
from chat_identities import BUILTIN, IDENTITIES
from chat_registry import Registry, LABELS
from chat_visibility import normalize, inherited


def registry(tmp_path, **data):
    cfg = dict(backends={'alpha': dict(type='api', protocol='openai-chat', enabled=True, mock=True, base_url='http://127.0.0.1:9/v1', model='a-1'),
                         'beta': dict(type='api', protocol='openai-chat', enabled=True, mock=True, base_url='http://127.0.0.1:9/v1', model='b-1')},
               default_owners={'deepseek': 'alpha'}, private_dir=str(tmp_path / '.multichat-private'))
    if data: (tmp_path / 'model_registry.json').write_text(json.dumps(dict(version=1, **data)), 'utf-8')
    return Registry(tmp_path, cfg)


def test_participants_are_listed_and_published(tmp_path):
    r = registry(tmp_path, participants={'mimo': dict(label='MiMo', color='#c2477f', connection='beta', model='b-1', created_at=1.0),
                                         'gone': dict(label='Gone', connection='beta', retired=True)})
    assert [p['id'] for p in r.participants()] == [*BUILTIN, 'mimo', 'gone']
    assert r.active_ids() == [*BUILTIN, 'mimo']
    assert list(IDENTITIES) == [*BUILTIN, 'mimo', 'gone'] and LABELS['mimo'] == 'MiMo'
    assert r.dynamic_owners() == {'mimo': 'beta'}                       # speaks through its own connection, in every room
    assert r.defaults()['deepseek'] == 'alpha' and r.defaults()['mimo'] == 'beta'
    assert r.participant_settings('mimo', 'beta') == {'model': 'b-1'}   # its own model choice …
    assert r.participant_settings('mimo', 'alpha') == {}                 # … only on its own connection
    assert normalize(['mimo', 'claude']) == ['claude', 'mimo']           # a valid private audience
    assert normalize(['gone']) == ['gone']                               # retired ids still resolve in old messages
    with pytest.raises(ValueError): normalize(['nobody'])
    assert inherited([{'metadata': {'visibility': ['mimo', 'claude']}}, {'metadata': {'visibility': ['mimo']}}]) == ['mimo']


def test_builtin_identity_is_not_borrowed(tmp_path):
    """The earlier workaround (a second provider bound to DeepSeek) is no longer how a model is added."""
    r = registry(tmp_path)
    r.data['participants']['qwen'] = dict(label='Qwen', connection='beta', model='b-1')
    r.refresh()
    by_id = {p['id']: p for p in r.participants()}
    assert by_id['deepseek']['connection'] == 'alpha' and by_id['qwen']['connection'] == 'beta'
    assert not by_id['qwen']['builtin'] and by_id['deepseek']['builtin']


def test_effort_fits_the_model(tmp_path):
    r = registry(tmp_path)
    r.data['catalog']['alpha']['a-2'] = dict(label='A2', effort_values=['low', 'high', 'max'], default_effort='high')
    assert r.default_effort('alpha', 'a-2', 'none') == 'high'       # not accepted: fall back to the model's stated default
    assert r.default_effort('alpha', 'a-2', 'low') == 'low'
    assert r.default_effort('alpha', 'a-1', 'whatever') == 'whatever'  # no declared levels: nothing to check against
