"""Prompt-cache hit rate of a reply, from the usage each provider returns.

Providers count cached input differently:
  anthropic-messages, claude CLI   input_tokens excludes cached input:
                                   total = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
  openai-chat                      prompt_tokens includes cached input: prompt_tokens_details.cached_tokens (OpenAI),
                                   prompt_cache_hit_tokens (DeepSeek)
  openai-responses                 input_tokens includes input_tokens_details.cached_tokens
  google-generate-content          promptTokenCount includes cachedContentTokenCount
  codex CLI                        input_tokens includes cached_input_tokens
A response without cache information counts as unknown, never as 0 %.
"""


def _int(value):
    return value if type(value) is int and value >= 0 else None


def one(kind, usage):
    """(total input, cached, written or None) for one response, or None when the provider gave no cache information."""
    u = usage if isinstance(usage, dict) else {}
    if kind in ('anthropic-messages', 'claude-cli'):
        base, read, made = _int(u.get('input_tokens')), _int(u.get('cache_read_input_tokens')), _int(u.get('cache_creation_input_tokens'))
        if base is None or read is None: return None
        return base + read + (made or 0), read, made
    if kind == 'openai-chat':
        total = _int(u.get('prompt_tokens'))
        cached = _int((u.get('prompt_tokens_details') or {}).get('cached_tokens')) if isinstance(u.get('prompt_tokens_details'), dict) else None
        if cached is None: cached = _int(u.get('prompt_cache_hit_tokens'))
        return (total, cached, None) if total is not None and cached is not None else None
    if kind == 'openai-responses':
        total = _int(u.get('input_tokens'))
        details = u.get('input_tokens_details') if isinstance(u.get('input_tokens_details'), dict) else {}
        cached = _int(details.get('cached_tokens'))
        return (total, cached, None) if total is not None and cached is not None else None
    if kind == 'google-generate-content':
        total, cached = _int(u.get('promptTokenCount')), _int(u.get('cachedContentTokenCount'))
        return (total, cached, None) if total is not None and cached is not None else None
    if kind == 'codex-cli':
        total, cached = _int(u.get('input_tokens')), _int(u.get('cached_input_tokens'))
        return (total, cached, None) if total is not None and cached is not None else None
    return None


def summarize(kind, usages, subscription=False):
    """Totals over every request of one reply. rate is a percentage, or None when unknown."""
    rows = [one(kind, usage) for usage in usages or []]
    known = [r for r in rows if r]
    total = sum(r[0] for r in known)
    written = [r[2] for r in known if r[2] is not None]
    result = dict(kind=kind, requests=len(rows), known_requests=len(known), input_tokens=total if known else None,
                  cached_tokens=sum(r[1] for r in known) if known else None, written_tokens=sum(written) if written else None,
                  rate=round(100 * sum(r[1] for r in known) / total, 1) if known and total else None)
    if subscription: result['subscription'] = True   # informational: says nothing about the subscription's quota
    return result


def recent(tasks, backend, limit=20):
    """Average hit rate over a connection's latest replies that reported cache information (token-weighted)."""
    rows = [t.get('cache') for t in tasks if t.get('backend') == backend and t.get('state') == 'completed'
            and isinstance(t.get('cache'), dict) and t['cache'].get('input_tokens')]
    rows = rows[-limit:]
    if not rows: return None
    total = sum(r['input_tokens'] for r in rows)
    return dict(replies=len(rows), rate=round(100 * sum(r['cached_tokens'] or 0 for r in rows) / total, 1) if total else None,
                subscription=any(r.get('subscription') for r in rows))
