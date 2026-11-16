import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from chat_api import APIBackend

def config(limit):
    return dict(type='api',protocol='openai-chat',identity='deepseek',enabled=True,mock=True,
                base_url='http://127.0.0.1:1/v1',model='mock',max_requests=limit,
                timeout=60,max_output_tokens=1024,max_requests_per_task=1)

def test_unlimited_preserves_ledger_and_concurrent_count(tmp_path):
    backend=APIBackend('deepseek',config(3),tmp_path)
    backend.usage.update(requests=3,completed=3,prompt_tokens=900,completion_tokens=123)
    backend.save()
    with pytest.raises(ValueError):backend.reserve()
    unlimited=APIBackend('deepseek',config(None),tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(lambda _:unlimited.reserve(),range(20)))
    restarted=APIBackend('deepseek',config(None),tmp_path)
    assert restarted.usage==dict(requests=23,completed=3,prompt_tokens=900,completion_tokens=123,unknown_usage=0)
    restarted.reserve()
    assert json.loads(restarted.path.read_text())['requests']==24
    assert restarted.config['timeout']==60 and restarted.config['max_output_tokens']==1024 and restarted.config['max_requests_per_task']==1

@pytest.mark.parametrize('limit',[False,True,0,-1,1.5,'unlimited'])
def test_invalid_limit_rejected(tmp_path,limit):
    with pytest.raises(ValueError):APIBackend('deepseek',config(limit),tmp_path)
