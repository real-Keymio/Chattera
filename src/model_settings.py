"""Per-call configuration, never native CLI global settings."""
import copy
import re
from chat_secrets import redact

API_TYPES={'openai-compatible','api'}
import cli_adapters
# Effort levels each subscription CLI accepts, from its adapter (the CLI's own flag, not inferred from model names).
CLI_EFFORT={name:list(cli_adapters.get(name).effort_values) for name in cli_adapters.names()}

def validate_settings(cfg,changes,backend):
    if not isinstance(changes,dict) or set(changes)-{'model','effort','thinking'}:raise ValueError('Only model, effort and thinking may be overridden')
    changes=copy.deepcopy(changes)
    if redact(changes)!=changes:raise ValueError('Secret value is not a model setting')
    if 'model' in changes and (not isinstance(changes['model'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}',changes['model'])):raise ValueError('Invalid model ID')
    effort=changes.get('effort')
    if effort is not None and (not isinstance(effort,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',effort)):raise ValueError('Invalid effort')
    effective=dict(cfg,**changes)
    if backend=='legacy' and changes:raise ValueError('Legacy backend does not support per-room or per-call model settings')
    effort=effective.get('effort',effective.get('reasoning_value'))
    capabilities=cfg.get('model_capabilities',{}).get(effective.get('model'),{})
    levels=capabilities.get('effort_values')
    if levels is None and cfg.get('type') not in API_TYPES:levels=CLI_EFFORT.get(backend)  # CLI default when the model has no declared levels
    if levels is not None and effort is not None and effort not in levels:raise ValueError('Effort not in explicitly configured model capabilities')
    if cfg.get('type') not in API_TYPES:
        if 'thinking' in changes:raise ValueError('CLI uses its native effort option, not API thinking')
    else:
        from api_protocols import protocol
        p=protocol(cfg);thinking=effective.get('thinking')
        if p=='openai-chat' and (thinking is not None or effort is not None and cfg.get('reasoning_parameter')!='reasoning_effort'):
            raise ValueError('Unsupported OpenAI reasoning mapping')
        if thinking is not None:
            if not isinstance(thinking,dict):raise ValueError('Thinking must be a protocol-specific object or null')
            if p=='anthropic-messages':
                if set(thinking)-{'type','budget_tokens'} or thinking.get('type') not in ('enabled','disabled','adaptive'):raise ValueError('Invalid Anthropic thinking')
                if thinking['type']=='enabled':
                    budget=thinking.get('budget_tokens')
                    if type(budget) is not int or not 1024<=budget<cfg.get('max_output_tokens',2048):raise ValueError('Thinking budget must be >=1024 and below output limit')
                elif 'budget_tokens' in thinking:raise ValueError('Only enabled thinking accepts budget_tokens')
            if p=='google-generate-content':
                if set(thinking)-{'thinkingBudget','includeThoughts','thinkingLevel'}:raise ValueError('Invalid Google thinkingConfig')
                if 'thinkingBudget' in thinking and (type(thinking['thinkingBudget']) is not int or not -1<=thinking['thinkingBudget']<=32768):raise ValueError('Invalid thinkingBudget')
                if 'includeThoughts' in thinking and type(thinking['includeThoughts']) is not bool:raise ValueError('Invalid includeThoughts')
                if 'thinkingLevel' in thinking and (not isinstance(thinking['thinkingLevel'],str) or not re.fullmatch(r'[A-Za-z_]{1,40}',thinking['thinkingLevel'])):raise ValueError('Invalid thinkingLevel')
                if 'thinkingBudget' in thinking and ('thinkingLevel' in thinking or effective.get('effort') is not None):raise ValueError('Use one Google thinking selector')
                if 'thinkingLevel' in thinking and effective.get('effort') is not None:raise ValueError('Use effort or thinkingLevel, not both')
        if p=='google-generate-content':
            level=effort if effort is not None else (thinking or {}).get('thinkingLevel')
            if level is not None and level not in ('MINIMAL','LOW','MEDIUM','HIGH','XHIGH'):
                raise ValueError('Google REST thinkingLevel requires an explicit uppercase enum supported by the selected model')
    return changes

def merge(cfg,room,override,backend,participant=None):
    """Connection defaults, then the participant's own defaults, the room's choice, and this message's choice."""
    value=copy.deepcopy(cfg)
    for changes in (participant,room,override):
        value.update(validate_settings(value,changes or {},backend))
    model_caps=value.get('model_capabilities',{}).get(value.get('model'),{})
    if value.get('model') in value.get('model_contexts',{}):value['context']=dict(value['model_contexts'][value['model']])
    for name in ('tools','images'):
        if name in model_caps:value[name]=bool(model_caps[name])
    return value
