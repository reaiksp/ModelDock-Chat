# 私有记忆模型实验 / Private memory model experiment

MDC 0.5.0 · 2026-11-02 · [MDC](https://github.com/reaiksp/ModelDock-Chat) 是 [MD](https://github.com/reaiksp/ModelDock-Windows) 的独立 SUB。

## 中文

### 这次尝试能做什么

MDC 把用户资料、已确认长期记忆、可选的人格提示词及当前续聊摘要保存到一个新的本机 Ollama 模型配置中。其他客户端选择该模型时，可以使用它的默认系统提示。MDC 自身每轮从实时数据库构造系统提示，因此不依赖旧快照，也不会将同一记忆重复叠加。每次快照有独立名称，原始基础模型保留。

**这不改变权重。** Ollama 的 `system` 是默认系统提示，`messages` 是示例历史；二者都不是学习进权重的训练接口。快照内容仍占用推理上下文。其他客户端如显式发送自己的系统提示，可能覆盖模型默认提示；记忆效果也取决于模型对提示的遵循能力。本实验使用默认系统提示，不把全部历史固化成示例消息。

### 如何使用

1. 在主聊天连接本机 Ollama 模型；API 模式可继续聊天和整理摘要，但不能创建本地模型快照。
2. 在“记忆与用户”编辑资料，审核长期记忆；未经确认的候选不会写入快照。
3. 在私有模型实验区选择是否包含人格，先预览内容、预算及遗漏数量，再创建快照。
4. 创建成功后可在实验区查看受管快照；其他 Ollama 客户端可选择其名称使用。快照不会自动替换 MDC 的当前聊天模型或 API 供应商。
5. “启用并保存私有模型”成功后会开启自动更新，可随时取消勾选。上下文续聊后，以及记忆、资料或人格变化后的下次本地发送，会尝试创建新版本。失败保留上一有效版本，聊天原文不被改写。
6. 不再需要时，删除相应受管快照。原模型不在实验区删除范围内。

预览和创建在当前上下文预算内选取完整记忆，不从中截断一个事实；未选入数量会显示。人格或资料等必要内容无法容纳时会提示调整，不宣称无限记忆。预览反映创建时的数据；后续更改需要更新快照。

### 上下文满时的自动处理

有完整历史记录时，发送前估算达到**可用输入预算 90%**，且有摘要空间时，MDC 会先用当前聊天供应商独立概括历史，再保存摘要检查点并建立带父会话关联的续聊。输入预算已经扣除回复与安全余量，长度采用保守 UTF-8 字节估算，不是精确 tokenizer 数。

检查点记录可查询的续聊摘要；原始消息继续保存在旧会话。摘要是可能丢失细节的参考材料，不会自动变成“用户已确认的事实”。新消息本身超限时仍阻止发送并保留输入；概括失败或停止时不能把丢失历史当作成功续聊。摘要请求也按上下文检查和分块，不能等超限后向模型塞入完整长历史。

API 聊天的概括使用当前选定的 API，相关历史会发送给该供应商；这与本地快照不上传是两件独立的事。API 模式不调用供应商的训练或模型创建接口。

### 隐私、修改与删除

- 私有快照中的个人资料和提示文本保存在本机 Ollama 模型配置内，**不是加密存储**；本机能够访问 Ollama 的程序可能读取它。
- 不要把个人快照当作可公开分享的通用模型。MDC 不会把它上传到模型平台，也不会把 API Key 写入快照。
- 删除或修改数据库中的记忆后，MDC 后续请求使用更新后的记忆记录；旧聊天、已有续聊摘要和外部模型快照仍可能保留旧内容，需要单独处理。更新为新名称也不会抹除旧版本。
- 原黑话词库导出与黑话派生模型保持原有范围，不因启用实验混入个人记忆。
- 摘要、原聊天、人格与记忆均独立保存；原输入和偏好不因界面语言切换而翻译改写。数据库自身仍未加密。

### 真正写进权重还缺什么

真正修改模型权重需要在模型外进行训练：清洗且经授权的训练数据、匹配的模型架构与训练框架、足够计算资源，以及训练前后的准确性、遗忘和隐私评估。单次聊天概括不能视为完成了训练。微调也不保证可靠储存任意用户事实；旧事实的定点删除通常比删除配置困难。

本版不自动运行训练、下载训练权重或消耗第三方训练额度。当前官方 Ollama 主线已不再支持旧式 LoRA adapter 创建路径，不能承诺任意模型可直接附加适配器；需要根据实际安装版本和模型格式重新验证。训练产物的导入与本实验的系统提示快照是不同流程。

## English

### What is persisted

A private snapshot saves the user profile, approved long-term memories, an optional personality prompt and the current continuation summary as the default system prompt of a new local Ollama model. Each update has a new name and preserves the base model. Other clients can use the default by selecting that model. MDC itself builds an explicit system prompt from its live database on each request, avoiding reliance on stale snapshots or duplicate insertion.

This does **not** modify weights or train the model. Ollama `system` stores default instructions; `messages` stores example history. Both still consume inference context. A client that sends an explicit system prompt may override the default. This experiment stores a system prompt, not an ever-growing copy of the complete conversation.

### Use and automatic refresh

Connect local Ollama, review and approve memories under Memory & user, choose whether to include the personality, and preview the snapshot before creating it. The preview reports budget usage and omitted memory counts. Memories are selected as complete records; oversized required content prompts the user to shorten it. Successful creation is checked against the saved prompt.

The Save private model and enable updates button turns on automatic refresh after successful creation; the checkbox can disable it. Automatic refresh runs after a continuation and on the next local send after memory, profile or personality changes. Failed creation preserves the last valid snapshot. The selected chat model and API provider are not silently changed. The experiment can delete its managed snapshots without deleting the base model.

### Continuation checkpoints

With complete history present, a send reaching **90% of the available input budget**, with room available for a summary, triggers a separate summary request using the selected chat backend. MDC saves a queryable checkpoint and creates a linked continuation while retaining the original messages. Input budgeting reserves reply and safety space and uses conservative UTF-8 estimates, not an exact tokenizer. Summary requests are budgeted and chunked too.

Summaries are potentially incomplete reference data, never automatically approved user facts. A single oversized message remains blocked without deleting its text. Cancellation or summary failure must not discard history. API summaries send the relevant history to the selected provider; API mode does not create a provider model or run provider training.

### Privacy and limitations

Personal snapshots are local but not encrypted. Other programs with access to local Ollama may read their system prompts. Do not publish a personal snapshot. MDC does not upload snapshots or place API keys in them. Deleting a database memory does not erase copies in past chat messages, continuation summaries or previously created models: handle these copies separately. A new version also leaves older versions intact.

Existing slang variants and vocabulary exports continue to exclude personal memories. Raw messages, personality text and preferences are preserved independently of UI language. The chat database remains unencrypted.

Actual weight training is a separate process requiring authorized, curated examples, a compatible model and framework, sufficient resources, and evaluations of accuracy, forgetting and privacy. It is not performed in this version. Current official Ollama main no longer supports the old LoRA-adapter creation path; validate the installed runtime and model format before planning trained-weight imports. Fine-tuning is not a guarantee of reliable factual storage or selective deletion.

## 官方参考 / Official references

核对日期 / Checked: 2026-11-02. 实际安装版本可能不同 / Installed versions may differ.

- [Ollama Create API](https://docs.ollama.com/api/create): `system`, `messages`, `from`, parameters.
- [Ollama Modelfile](https://docs.ollama.com/modelfile): defaults, examples and `num_ctx`.
- [Ollama context length](https://docs.ollama.com/context-length): context limits and memory cost.
- [Ollama model import](https://docs.ollama.com/import): separately prepared Safetensors/GGUF weights.
- [Official request handling](https://github.com/ollama/ollama/blob/main/server/routes.go): `chatMessagesForRequest` and explicit system-prompt behavior.
- [Official create implementation](https://github.com/ollama/ollama/blob/main/server/create.go): current adapter compatibility boundary.
