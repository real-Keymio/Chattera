"""Model-bound token admission. Never infer a relay's window from model names."""
import math
import re

API_TYPES = {'openai-compatible', 'api'}
# Share of the computed room kept free, because the token count below is an estimate, not the provider's tokenizer.
MARGIN = .85
# CLIs keep their own context inside one reply (they compact it when their tool loop grows); API replies resend
# every tool result in Chattera's own loop, so they keep room for them.
API_TOOL_RESERVE = 208512
CLI_OUTPUT = 32000


def count(text):
    """Estimated tokens. No official tokenizer for the subscription/relay endpoints is available here.
    ASCII text runs about 4 characters a token; Chinese about one character a token. Both are rounded up
    (3.5 characters, 1.3 tokens) so that the estimate stays above the providers' own counts."""
    ascii_chars = len(text.encode('ascii', 'ignore'))
    return math.ceil(ascii_chars / 3.5 + (len(text) - ascii_chars) * 1.3)


def clip(text, tokens):
    """The longest start of text whose estimate stays within tokens."""
    if count(text) <= tokens: return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if count(text[:middle]) <= tokens: low = middle
        else: high = middle - 1
    return text[:low]


OVERFLOW = re.compile(r"prompt is too long|input is too long|context[_ ]length|context window|maximum context|too many (input )?tokens"
                      r"|exceeds? (the )?(model'?s? )?(maximum )?(context|input)|request too large", re.I)


def overflowed(message):
    """Whether a provider or CLI refused the request because the input did not fit the model."""
    return bool(OVERFLOW.search(message or ''))


def prompt_parts(prompt, snapshot):
    """(fixed part, rest) when the input was built with a split point and is unchanged since; otherwise None."""
    import hashlib
    split = (snapshot or {}).get('prompt_split') or {}
    size = split.get('chars')
    if type(size) is not int or not 0 < size < len(prompt): return None
    if hashlib.sha256(prompt[:size].encode('utf-8')).hexdigest() != split.get('sha256'): return None
    return prompt[:size], prompt[size:]


def requested_limit(mode='256k',custom=None):
    targets={'100k':100000,'256k':262144,'1m':1000000,'standard':20000,'long':100000,'maximum':1000000}
    if mode=='custom':
        if type(custom) is not int or not 1<=custom<=1000000:raise ValueError('Custom Token capacity must be an integer from 1 to 1000000')
        return custom
    if mode not in targets:raise ValueError('Unknown context mode')
    return targets[mode]


def reserves(cfg, tools):
    """Output and tool room taken from the window before history: (output, tools)."""
    api = cfg.get('type') in API_TYPES
    output = cfg.get('max_output_tokens', 128000 if api else CLI_OUTPUT)
    return output, (API_TOOL_RESERVE if tools and api else 0)


def policy(cfg, tools=False, mode='maximum',custom=None):
    target=requested_limit(mode,custom)
    declared=cfg.get('context',{})
    if not declared:return None
    if declared.get('model')!=cfg.get('model'):
        declared={'window_tokens':32768,'source':'manual model: unverified provisional cap','status':'UNKNOWN'}
    window=declared.get('window_tokens',32768)
    if type(window) is not int or not 4096<=window<=2_000_000:raise ValueError('Invalid backend context window')
    output, tool_reserve = reserves(cfg, tools)
    overhead=declared.get('backend_reserve',32000)
    if type(output) is not int or output<1 or type(overhead) is not int or overhead<0:
        raise ValueError('Invalid output or backend reserve')
    if window<overhead+output+tool_reserve:
        raise ValueError('Model context is unverified or too small for reserved tools/output; configure its capacity')
    return dict(context_mode=mode,target_tokens=target,native_window_tokens=declared.get('native_window_tokens','UNKNOWN'),
        backend_window_tokens=window,window_source=declared.get('source','operator configured'),
        window_status=declared.get('status','UNKNOWN'),backend_reserve_tokens=overhead,
        output_reserve_tokens=output,tool_reserve_tokens=tool_reserve,
        initial_budget_tokens=min(target,int((window-overhead-output-tool_reserve)*MARGIN)),
        count_method='estimate: ASCII 3.5 characters a token, other characters 1.3 tokens each; 15% of the room kept free',
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
