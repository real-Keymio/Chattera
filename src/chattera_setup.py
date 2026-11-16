"""First-run setup, demo mode, local user guides, extra CLI search paths and quitting — the parts of Chattera that sit
around the chat. Nothing here changes how chats, permissions, snapshots, history or the library work.

Demo mode is one ordinary chat whose participants answer through the built-in `demo` backend: canned replies written
for the tour, no model is called and no key is needed. Other chats and the user's real connections are untouched;
deleting the demo chat removes it like any other.
"""
import json
import os
import threading
import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

from chat_backends import MockBackend
from chattera_paths import Home, program_dir, portable

GUIDES = {'start': '01_先看这个.pdf', 'full': '02_Chattera完整使用指南.pdf'}
DEMO_NAME = '演示：认识 Chattera'

DEMO_REPLIES = {
    'claude': ("（演示回复，不是真实模型的回答）\n\n你好！这里是一个**群聊**：你提一个问题，房间里的几位 AI 会各自回答。"
               "你可以只问其中一位（点输入框上方的名字），也可以让大家都回答。\n\n"
               "- 点一条回答右上角的菜单，可以让别人**接着回应**它；\n- 选中一段文字，可以**引用**它继续追问；\n"
               "- 打开“独立回答”，每位会先各自作答，互相看不到对方，适合比较不同观点。"),
    'codex': ("（演示回复，不是真实模型的回答）\n\n补充一点：每条回答下面会写明它用的是**哪个连接、哪个模型**。"
              "真实使用时，这些回答来自你自己添加的 API 连接，费用由对应的服务商按用量收取；Chattera 本身免费。"),
    'antigravity': ("（演示回复，不是真实模型的回答）\n\n想对比阅读？顶部的布局按钮可以把对话分成 **2–6 栏**，"
                    "也可以切到**自由画布**：把整轮讨论、单条消息或选中的段落拖到画布上，自由排列。"),
    'deepseek': ("（演示回复，不是真实模型的回答）\n\n准备好用真的了吗？打开左下角的**设置 → 模型与服务**，添加一个 API 连接"
                 "（例如 DeepSeek），填入你从服务商网站拿到的 API Key，然后新建一个聊天提问即可。"
                 "不需要演示聊天时，可以在左侧列表里把它归档，不影响别的内容。"),
}
GENERIC = '（演示回复，不是真实模型的回答）\n\n这是演示模式下的预设回答。添加一个真实的 API 连接后，这里会是模型自己的回答。'


class DemoBackend(MockBackend):
    """Canned, clearly labelled replies for the demo chat. Never calls a model or the network."""
    def run(self, prompt, task, attach, cancel):
        if cancel.wait(.6):
            raise TimeoutError('Cancelled')
        identity = task.get('identity') or task.get('model') or ''
        return dict(text=DEMO_REPLIES.get(identity, GENERIC), model='demo', requested_model='demo', usage='UNKNOWN', rate_limits='UNKNOWN',
                    usage_fields=dict(input_tokens=0, output_tokens=0, subscription_debit='none', billed_amount='none'))


def install(runtime, app_module, data_dir):
    api = app_module.app
    home = Home(os.environ.get('CHATTERA_HOME') or Path(data_dir).resolve().parent)
    runtime.tasks.backends['demo'] = DemoBackend()

    def settings():
        return home.settings()

    def save_settings(value):
        home.config.mkdir(parents=True, exist_ok=True)
        temp = home.settings_file.with_suffix('.tmp')
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=1), 'utf-8')
        temp.replace(home.settings_file)

    def usable():
        names = []
        for name, runner in runtime.tasks.backends.items():
            if name in ('mock', 'demo'): continue
            try:
                if not hasattr(runner, 'available') or runner.available(): names.append(name)
            except Exception:
                pass
        return names

    def demo_room():
        return next((r for r in runtime.rooms.list() if r.get('demo') and not r['archived']), None)

    def guide_path(key):
        name = GUIDES.get(key)
        if not name: return None
        for folder in (program_dir(), program_dir() / 'docs' / 'user-guide' / 'dist'):
            if (folder / name).is_file(): return folder / name
        return None

    def home_display():
        """The data folder as people can type it into Explorer, without spelling out the Windows user name."""
        root = str(home.root)
        if portable():
            return root.replace(str(program_dir()), '<程序文件夹>', 1)
        for var in ('LOCALAPPDATA', 'APPDATA', 'USERPROFILE'):
            base = os.environ.get(var)
            if base and root.lower().startswith(base.rstrip('\\/').lower() + os.sep):
                return '%' + var + '%' + root[len(base.rstrip('\\/')):]
        return root

    @api.get('/api/chattera/setup')
    async def status():
        s = settings()
        connections = usable()
        return dict(app='Chattera', version=os.environ.get('CHATTERA_VERSION', 'UNKNOWN'), home=home_display(), portable=portable(),
                    connections=connections, needs_setup=not connections and not s.get('setup_done'),
                    demo_room=(demo_room() or {}).get('channel'), guides={k: bool(guide_path(k)) for k in GUIDES},
                    cli_search_paths=s.get('cli_search_paths', []))

    @api.post('/api/chattera/setup/done')
    async def done():
        s = settings(); s['setup_done'] = True; save_settings(s)
        return {'ok': True}

    @api.post('/api/chattera/demo')
    async def demo():
        """Open the demo chat, creating it the first time."""
        room = demo_room()
        if room is None:
            room = runtime.create(DEMO_NAME)
            with runtime.rooms.lock:
                stored = runtime.rooms.rooms[room['id']]
                stored['demo'] = True
                for pid in runtime.registry.active_ids():
                    stored['owners'][pid] = 'demo'
                runtime.rooms.save()
            runtime.sync_settings()
            await app_module.broadcast_settings()
            await app_module.broadcast_agents()
        return runtime.rooms.get(room['id'])

    @api.get('/api/chattera/guide/{key}')
    async def guide(key: str):
        path = guide_path(key)
        if path is None: return JSONResponse({'error': '找不到使用指南文件'}, status_code=404)
        return FileResponse(path, media_type='application/pdf', headers={'Content-Disposition': 'inline', 'Cache-Control': 'no-store'})

    @api.post('/api/chattera/cli-paths')
    async def cli_paths(request: Request):
        """Extra folders (or executables) to look in for subscription CLIs; used by the scan and at the next start."""
        body = await request.json()
        paths = body.get('paths')
        if not isinstance(paths, list) or len(paths) > 20 or any(not isinstance(p, str) or not p.strip() or len(p) > 400 for p in paths):
            return JSONResponse({'error': '每行一个文件夹路径，最多 20 个'}, status_code=400)
        s = settings(); s['cli_search_paths'] = [p.strip() for p in paths]; save_settings(s)
        return {'cli_search_paths': s['cli_search_paths']}

    @api.post('/api/chattera/quit')
    async def quit_app():
        """Stop the server (the browser window stays open and shows that Chattera has closed)."""
        def stop():
            time.sleep(.4)
            os._exit(0)
        threading.Thread(target=stop, daemon=True).start()
        return {'ok': True}

    # the local CLI scan also looks in the user's extra paths and reports sign-in status from the vendors' status commands
    from model_discovery import local_clis
    import asyncio

    @api.get('/api/chattera/cli-scan')
    async def cli_scan():
        return await asyncio.to_thread(local_clis, runtime.registry.backends(), runtime.tasks.backends,
                                       settings().get('cli_search_paths', []), True)
