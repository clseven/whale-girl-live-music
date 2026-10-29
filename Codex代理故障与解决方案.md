# Codex CLI 网络超时：原因与修复

记录日期：2026-10-27。

## 故障

Codex CLI 已通过 ChatGPT 登录，但刷新模型列表报 request timed out；直播预览的文本请求达到 60 秒超时。

## 定位

Windows 系统代理已启用，地址为 127.0.0.1:7897，但启动 Codex CLI 的进程没有 HTTP_PROXY / HTTPS_PROXY 环境变量。CLI 没有按预期使用 Clash。为同一 CLI 调用显式设置这两个变量后，成功返回文本。

另外，系统代理开启而 Clash 关闭时，依赖系统代理的其他应用可能无法联网。这与 CLI 未读取系统代理是两个相关但不同的问题。

## 项目修复

直播预览/local-config.json 固定 codex_proxy 为 http://127.0.0.1:7897。
server.py 在 subprocess.Popen 的 env 参数内，仅为 Codex 子进程设置 HTTP_PROXY / HTTPS_PROXY 及其小写形式；NO_PROXY 排除 localhost 和 127.0.0.1。不修改 Windows 系统代理、不写全局环境变量、不修改用户 Codex 全局配置。

每次调用先检查代理端口能否连接。Clash 未启动时返回明确的 503 提示，不静默直连。模型固定为 gpt-6.1-sol，推理强度 low。

## 使用

1. 启动 Clash，使其本地代理端口监听 7897。只为本程序调用代理时，不要求开启 Clash 的系统代理开关。
2. 运行直播预览/启动预览.cmd。
3. 浏览器打开 http://127.0.0.1:18763/。

仅使用系统代理的其他应用在关闭系统代理后可恢复自己的联网方式；开启 TUN 时其影响需另行判断。本次未更改系统代理和 TUN。端口改变时更新 local-config.json 并重启服务。上游网络、账号限额和 Clash 节点仍可能影响请求，专用代理不能保证永不超时。

## 验证

- 指定代理的 Codex CLI 返回“连接成功”。
- 完整 HTTP 链路成功返回中文回复与晓伊 MP3：文本 10.738 秒，语音生成 1.614 秒，总计 12.352 秒。这是一次样本，不代表长期平均。
- 空输入、超过500字输入返回400；原60秒超时分支已实际触发。
- 尚未完成真实直播接入、并发与内容边界的全面测试。


## 常驻和隔离配置修复

已改用 codex app-server 的 stdio JSON-RPC，initialize 一次、thread/start 一次，弹幕通过 turn/start 继续同一会话。原版鲸鱼娘提示逐字恢复，放在 baseInstructions 中替换默认编程助手说明；developerInstructions 为空。不通过改短角色提示提速。

直接读取用户全局配置时发现 MCP 加载错误：invalid transport in mcp_servers."codebase-memory-mcp"。修复方式是只给鲸鱼娘子进程设置独立 CODEX_HOME，位于直播预览/.runtime/codex-home；只从现有登录复制 auth.json，不复制全局 config.toml、AGENTS.md、插件、MCP、记忆和聊天历史。隔离目录中的登录刷新不写回原目录。目录包含凭据，已禁止通过本地HTTP访问，并用.gitignore排除，不应分享该目录。

连续两条真实调用验证：同一PID、resident_starts=1，第二条能回忆上一条。第一条首字5.469秒、文本完成7.16秒；第二条首字2.576秒、文本完成3.89秒。是少量样本，不代表平均。仍有约7千输入Token的运行时上下文；不能承诺只剩人设Token。清空上下文将新建会话，但保持进程常驻。语音仍是文本完成后合成，尚未实现流式语音。
