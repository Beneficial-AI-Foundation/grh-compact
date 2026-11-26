# Docker 隔离复核

运行 `python3 scripts/review_sketch_docker.py --dry-run`，先检查容器内只有一份复核输入，且两个本机原证明仓库路径不可见。运行 `python3 scripts/review_sketch_docker.py` 才会启动新的 Codex 复核会话；结果保存在 `summaries/reviews/<UTC 时间>/`。

容器使用本机已有的 `python:3.12-slim` 镜像和 Codex 二进制，根文件系统只读，去掉 Linux capabilities。只挂载输入目录、Codex 二进制和账号登录文件；不挂载本项目或原版证明仓库。每次运行保存输入、事件、标准错误、最终回答和审计 JSON，并检查冻结草图的 SHA-256。

这保证的是**容器文件系统隔离**。为了连接 Codex 服务，容器仍有网络；账号登录文件在容器内可读。因此审计记录还需检查是否出现工具或错误事件，不能仅凭文件预检就声称没有通过网络取得额外信息。若需更严格的无工具复核，应改用只发送文本、不给模型工具的 API 调用，并由调用程序保管凭据。
