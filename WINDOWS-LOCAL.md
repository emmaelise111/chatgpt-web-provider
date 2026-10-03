# 本机安装与 Chatbox 使用说明

启动 `start-provider.cmd`。需要重新登录时，先停止服务，再启动 `setup-browser.cmd`。
本机 Python 不读取 PYTHONPATH，脚本会显式加载项目的 src 和 .deps；浏览器使用已安装的 Chrome 和独立 chrome-profile。

- Base URL：`http://127.0.0.1:8791/v1`
- API Key：`.env` 中的 `CHATGPT_WEB_API_KEYS`
- 模型示例：`chatgpt-5.6-sol-high-web`。此名称是桥接别名，网页模型选择仍是尽力匹配。
- 超时建议至少 300 秒。SSE 仍在网页回答完成后发送，不是实时 token 流。

## 连续对话

Chatbox 通常会在每次请求的 messages 中带上历史记录。桥接器根据完整历史前缀匹配以前的网页会话，匹配成功后仅发送新增消息。
不同历史会创建不同网页会话；切换回已有历史时会重新打开对应的网页会话。
会话映射持久化到 `chrome-profile/provider-sessions.json`，只保存消息摘要、网页地址和随机 ID，不保存消息正文或图片 Base64。
匹配同时区分 API Key 和模型。两条会话历史完全相同时无法仅凭标准 messages 唯一识别，会创建新分支；可用显式 conversation_id 消除歧义。

非流式回复包含 `conversation_id`；流式响应头包含 `X-Conversation-Id`。
只发送最新一条用户消息的客户端必须传回 conversation_id，才能可靠续聊。
`new_session: true` 强制新会话；省略或 false 使用历史匹配，不会随意复用最后一个网页会话。
若客户端关闭历史记录或截断了历史且未提供 conversation_id，桥接器无法恢复客户端没有提供的上下文。
system/developer 消息仍通过提示词转写，不能等同原生 API 的角色权限。

## 图片

支持 user.content 中的 OpenAI 风格 `text` 与 `image_url` 部分。
图片必须是 `image_url.url` 中的 Base64 data URL。支持 PNG、JPEG、WebP、GIF；单图至多 20 MiB，每次请求至多 10 张、合计 32 MiB。
图片会解码为内存中的文件并上传至 ChatGPT 网页，不会写成 Base64 提示词。
同一网页会话续聊时不重复上传历史图片；创建新分支时会重放请求里携带的图片。
普通文档、file_id、远程图片 URL 和本机文件路径尚未实现，返回明确的 HTTP 400，不会转成乱码发送。

