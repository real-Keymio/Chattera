"""Human-only append operations; all model reads use the separate audience policy."""
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from chat_visibility import audience, normalize, effective, provenance


def validate(runtime, channel, meta, reply_to=None):
    scope = normalize(meta.get('visibility', 'public'))
    targets = meta.get('targets', [])
    if not isinstance(targets, list) or any(not isinstance(t, str) or t not in runtime.app.config['agents'] for t in targets):
        raise ValueError('Select stable model identities')
    if scope != 'public' and any(t not in scope for t in targets):
        raise ValueError('Every reply target must be in the audience')
    if reply_to is not None:
        parent = runtime.app.store.get_by_id(reply_to)
        if not parent or parent.get('channel', 'general') != channel:
            raise ValueError('Referenced message unavailable')
        parent_scope = audience(parent)
        if parent_scope != 'public' and (scope == 'public' or not set(scope).issubset(parent_scope)):
            raise ValueError('A private reference requires the same or narrower audience; use Share to publish')
    for target in targets:
        backend = runtime.rooms.owner(channel,target)
        if not runtime.tasks.capabilities(backend)['available']:
            raise ValueError('Selected backend unavailable')
    overrides=meta.get('model_overrides',{})
    if not isinstance(overrides,dict) or set(overrides)-set(targets):raise ValueError('Overrides must name explicit reply targets')
    for target,setting in overrides.items():runtime.tasks.effective_config(channel,target,setting)
    independent = meta.get('independent_mode')
    if independent not in (None, '', 'normal', 'strict'):
        raise ValueError('Invalid independent answer mode')
    if independent and len(set(targets)) < 2:
        raise ValueError('Independent answers require at least two models')
    if scope != 'public' or independent:
        involved = targets if scope == 'public' else set(targets) | set(scope)
        if any(runtime.rooms.owner(channel, t) not in runtime.tasks.backends for t in involved):
            raise ValueError('Private/independent discussion requires configured fresh-call backends')
    return scope


def install(runtime):
    app = runtime.app

    @app.app.post('/api/chat/message/{message_id}/{operation}')
    async def operation(message_id: int, operation: str, request: Request):
        try:
            body = await request.json()
            with runtime.tasks.lock:
                original = app.store.get_by_id(message_id)
                if not original or original.get('type', 'chat') != 'chat':
                    raise ValueError('Message unavailable')
                channel = original.get('channel', 'general')
                if runtime.rooms.get(channel)['archived']:
                    raise ValueError('Restore this room before changing messages')
                meta = original.get('metadata') or {}
                is_ai = app.registry.is_agent_family(original['sender'])
                history = app.store.get_since(-1, channel=channel)
                root = meta['version_root'] if meta.get('version_root') is not None else original['id']
                family = [m for m in history if ((m.get('metadata') or {}).get('version_root') if (m.get('metadata') or {}).get('version_root') is not None else m['id']) == root]
                latest = effective(family)[-1]
                if operation == 'versions':
                    return family
                if operation == 'share':
                    if body.get('confirm') is not True:
                        raise ValueError('Confirm the exact content before sharing')
                    group = meta.get('independent_group')
                    if group and any(t.get('independent_group') == group and t['state'] == 'running' for t in runtime.tasks.list(channel)):
                        raise ValueError('Wait for or cancel the remaining independent answers before sharing')
                    text = body.get('text', original['text'])
                    prior = provenance(original, app.store.get_by_id) or {}
                    attribution = dict(original_author=prior.get('original_author', runtime.family(original['sender'])),
                        original_message_id=prior.get('original_message_id', original['id']),
                        source_message_id=original['id'], shared_by=app.room_settings.get('username', 'user'),
                        human_edited=(text != original['text'] or prior.get('human_edited', False)),
                        semantic_role='quoted_content_not_user_instruction')
                    new_meta = dict(visibility='public', targets=[], shared_from=original['id'], share_provenance=attribution)
                    sender, reply_to = app.room_settings.get('username', 'user'), None
                elif operation in ('edit', 'restore'):
                    if operation == 'edit' and is_ai:
                        raise ValueError('Only human messages can be edited')
                    if operation == 'edit' and latest['id'] != message_id:
                        raise ValueError('This version was replaced; reload before editing')
                    text = body.get('text') if operation == 'edit' else original['text']
                    sender, reply_to = original['sender'], original.get('reply_to')
                    new_meta = dict(meta, visibility=audience(original), targets=[],
                                    version_root=root, replaces_message_id=latest['id'],
                                    version=len(family)+1, revision_event=operation)
                    if meta.get('task_id'):
                        new_meta['source_task_id'] = meta['task_id']
                    new_meta.pop('task_id', None)
                    new_meta.pop('independent_mode', None)
                    # Invocation overrides belong to the original send, not a
                    # non-waking version event. Original task audit retains them.
                    new_meta.pop('model_overrides', None)
                    if meta.get('shared_from') is not None:
                        new_meta['share_provenance'] = provenance(original, app.store.get_by_id)
                        if operation == 'edit':
                            new_meta['share_provenance'].update(human_edited=True, edited_by=app.room_settings.get('username', 'user'))
                elif operation == 'regenerate':
                    if not is_ai or not (meta.get('task_id') or meta.get('source_task_id')):
                        raise ValueError('Regeneration requires a recorded fresh-call reply')
                    old = runtime.tasks.tasks[meta.get('task_id') or meta['source_task_id']]
                    directory = runtime.tasks.directory / old['task_id']
                    snapshot = json.loads((directory / 'snapshot.json').read_text('utf-8'))
                    if 'history' not in snapshot:
                        raise ValueError('Old call has no frozen audience snapshot; send a new question')
                    if any((m.get('metadata') or {}).get('shared_from') is not None and not (m.get('metadata') or {}).get('share_provenance') for m in snapshot['history']):
                        raise ValueError('Old shared-content snapshot has no attribution; send a new question')
                    current = app.store.get_by_id(old['current_id'])
                    audit = {k: old[k] for k in ('room_id','message_ids','input_bytes','input_estimate','budget','summary_version')}
                    for key in ('attachment_sources','image_transport_bytes','image_processing','image_token_allowance','context_policy','budget_unit','estimate_method','reference_ids','history_included_ids','trimmed_history_ranges','snapshot_summary_version'):
                        if key in old: audit[key] = old[key]
                    return runtime.tasks.start(channel, old['identity'], current,
                        independent_mode=old.get('independent_mode'), participants=old.get('participants', [old['identity']]),
                        prepared=dict(snapshot=snapshot, prompt=(directory/'input.json').read_text('utf-8'), audit=audit),
                        reply_visibility=audience(original), replaces_message_id=latest['id'], version_root=root, message_version=len(family)+1,
                        regenerated_from=original['id'], independent_group=meta.get('independent_group'),
                        settings_override={k:v for k,v in old.get('configuration',{}).items() if k in ('model','effort','thinking')})
                else:
                    raise ValueError('Unknown message operation')
                if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                    raise ValueError('Message text is empty or too long')
                runtime.revision_write = True
                try:
                    return app.store.add(sender, text.strip(), channel=channel, reply_to=reply_to, metadata=new_meta,
                                         attachments=[] if operation == 'share' else original.get('attachments', []))
                finally:
                    runtime.revision_write = False
        except (ValueError, KeyError, OSError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
