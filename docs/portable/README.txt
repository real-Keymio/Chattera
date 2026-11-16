Chattera — A group chat for you and your AIs.
============================================

怎么开始
--------
1. 双击 “Chattera.exe”。第一次启动需要十几秒。
2. 浏览器窗口打开后，可以先点“进入演示”体验（不需要账号，不花钱），
   或者点“添加连接”，填入你从 AI 服务商那里拿到的 API Key，开始真实的聊天。
3. 详细步骤请看同一个文件夹里的两份 PDF：
   01_先看这个.pdf            —— 两页，从打开到第一次聊天
   02_Chattera完整使用指南.pdf —— 全部功能与常见问题

费用
----
Chattera 本身免费。使用真实的 AI 模型时，费用由你选择的模型服务商按用量收取。

你的数据在哪里
--------------
聊天、设置、API Key 和资料库保存在  %LOCALAPPDATA%\Chattera
（在资源管理器地址栏输入这一串并回车即可打开）。
替换或删除这个程序文件夹不会删除你的数据。

便携模式：在本文件夹里新建一个名为 portable.txt 的空文件，
之后所有数据都会保存在本文件夹下的 data 文件夹里（适合放在 U 盘中）。

退出
----
关闭浏览器窗口不会退出 Chattera。要退出，请在屏幕右下角通知区域的 Chattera 图标上
点右键 → “退出 Chattera”，或在应用内 “帮助 → 退出 Chattera”。

其他
----
- 只在本机运行（127.0.0.1），不要把它暴露到局域网或互联网。
- 许可：MIT（LICENSE.txt）；第三方组件见 THIRD_PARTY_NOTICES.txt 和 licenses 文件夹。
- 仅支持 Windows 10/11 64 位。

English: double-click Chattera.exe. Try the demo, or add an API connection in Settings.
Your data lives in %LOCALAPPDATA%\Chattera (or .\data when portable.txt exists next to Chattera.exe).
Chattera is free; AI providers bill you directly for API usage.
