# 模型存储位置 / Model storage locations

MD部署工作台与MDC「语音与部署」都可选择文字模型目录，点击「应用目录并重启服务」生效。MDC还可单独选择语音模型文件目录；更换前先停止语音服务。路径支持空格与中文。更换文字目录会短暂重启本机Ollama，请先结束聊天和下载。

先复制并逐文件SHA-256核对，再重启服务、核对模型列表和实际权重路径，成功后才保存选择。不同的同名文件不覆盖，嵌套源／目标目录、符号链接和目录联接拒绝迁移；取消或失败保留原文件，失败重启尝试恢复原服务。已有模型通过新目录继续调用。应用默认保留旧副本，用户确认新目录可用后再清理旧模型目录；不要删除整个`.ollama`，其中有身份密钥等非模型数据。

修复的问题：给CLI设置`OLLAMA_MODELS`不会改变已经启动的Ollama服务。本版实际重启并读取服务的模型权重路径，目录不匹配时不继续下载。用户级目录设置与传给新服务的环境变量同步。文字下载缓存`.modeldock-gguf`也位于所选文字模型目录。共享选择保存在当前用户`ModelDockRuntime/storage.json`；MD/MDC聊天、人格、记忆、API密钥各自保留，不参与迁移。

语音只移动模型权重与下载目录。Python运行环境、程序日志和聊天资料仍在应用数据目录；不移动已创建的虚拟环境，以免破坏其路径。联合推荐分别核算文字模型盘、语音模型盘与运行环境盘，避免以C盘空间错误代替E盘模型空间。仍须满足内存、显存和实际模型加载要求。

依据：[Ollama存储目录说明](https://docs.ollama.com/faq#how-do-i-set-them-to-a-different-location)、[Windows环境变量与重启说明](https://docs.ollama.com/faq#setting-environment-variables-on-windows)。

## English

Choose a text-model folder in MD or MDC, then apply it to migrate verified files and restart local Ollama. MDC also offers a separate speech-weight folder; stop speech before changing it. Existing files are copied and checked withSHA-256. Different same-name files are never overwritten. Nested paths, symlinks and junctions are refused. Cancellation/failure retains source files, and a failed server change attempts to restore the old service. Successful changes verify the model list and actual weight directory before saving shared user settings. Old copies are retained by the application.

Setting`OLLAMA_MODELS`on aCLI cannot redirect an already running server. This update restarts the server and verifies its actual model path, and blocks downloads when the configured path does not match. MD and MDC share location settings, not personal chat data. Speech weights can use another disk; its Python environment and application data stay in place. Text-model, speech-model and runtime disk budgets are checked separately. Changing disks does not remove RAM/VRAM requirements.
