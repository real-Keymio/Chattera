"""Model-bound token admission. Never infer a relay's window from model names."""
def count(text):
    # No official tokenizer for the subscription/relay endpoints is available in
    # this installation. UTF-8 byte fallback conservatively counts one token per
    # byte; this is an admission estimate, NOT reported provider usage.
    return len(text.encode('utf-8'))

def requested_limit(mode='256k',custom=None):
    targets={'100k':100000,'256k':262144,'1m':1000000,'standard':20000,'long':100000,'maximum':1000000}
    if mode=='custom':
        if type(custom) is not int or not 1<=custom<=1000000:raise ValueError('Custom Token capacity must be an integer from 1 to 1000000')
        return custom
    if mode not in targets:raise ValueError('Unknown context mode')
    return targets[mode]

def policy(cfg, tools=False, mode='maximum',custom=None):
    target=requested_limit(mode,custom)
    declared=cfg.get('context',{})
    if not declared:return None
    if declared.get('model')!=cfg.get('model'):
        declared={'window_tokens':32768,'source':'manual model: unverified provisional cap','status':'UNKNOWN'}
    window=declared.get('window_tokens',32768)
    if type(window) is not int or not 4096<=window<=2_000_000:raise ValueError('Invalid backend context window')
    output=cfg.get('max_output_tokens',128000)
    overhead=declared.get('backend_reserve',32000)
    if type(output) is not int or output<1 or type(overhead) is not int or overhead<0:
        raise ValueError('Invalid output or backend reserve')
    tool_reserve=208512 if tools else 0  # 8 bounded results * 6500 Unicode chars * 4 + framing
    if window<overhead+output+tool_reserve:
        raise ValueError('Model context is unverified or too small for reserved tools/output; configure its capacity')
    return dict(context_mode=mode,target_tokens=target,native_window_tokens=declared.get('native_window_tokens','UNKNOWN'),
        backend_window_tokens=window,window_source=declared.get('source','operator configured'),
        window_status=declared.get('status','UNKNOWN'),backend_reserve_tokens=overhead,
        output_reserve_tokens=output,tool_reserve_tokens=tool_reserve,
        initial_budget_tokens=min(target,window-overhead-output-tool_reserve),
        count_method='conservative UTF-8 byte upper estimate; no endpoint tokenizer available',
        actual_input_tokens='UNKNOWN')

def image_tokens(snapshot,payload):
    # Conservative per-pixel allowance, separate from base64 bytes. Not a claim
    # that providers bill this number. Runtime usage remains authoritative.
    records=[snapshot['attachment_content'][f"{a['message_id']}:{a['index']}"] for a in payload.get('attachment_evidence',[]) if a.get('kind')=='image']
    return sum(4096+((v.get('prepared_size',[v.get('width',0),v.get('height',0)])[0]+31)//32)*
        ((v.get('prepared_size',[v.get('width',0),v.get('height',0)])[1]+31)//32)*4 for v in records)

def check(prompt,snapshot,payload):
    p=snapshot.get('context_policy')
    if p and count(prompt)+image_tokens(snapshot,payload)>p['initial_budget_tokens']:raise ValueError('Token input and image allowance exceed model context budget')
