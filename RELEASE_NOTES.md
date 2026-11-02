# ModelDock Chat 0.7.0 · 双需求条与记忆黑话验证

- 同参数精简到 BF16 完全体五档，与对话长度／记忆需求五档共同推荐。
- 所选目标与实际推荐分开显示，联合语音、上下文、内存和双份磁盘缓存预算。
- 拖动不下载、不切换聊天模型；保留用户主动点击下载按钮。API 后端保持不变。
- MD 部署方案导入同步需求、上下文与语音；按模型保存记忆策略，不改用户原文。
- 私有默认提示快照支持已确认记忆和黑话共同保存，仍非权重训练。

## 本次验证 / Validation

- 完整回归 247 项测试通过；补充 API 同名模型隔离后，76 项相关回归通过，原生进程均正常退出。
- 打包后的独立程序启动、中英文界面、双滑条范围、主动下载入口、语音桥接和音频文件释放检查通过。
- MD / MDC 共 2,025 组硬件、精度、对话需求和语音组合的推荐结果一致；固定 GGUF 来源表一致。独立原生界面检查覆盖中英文、双滑条和主动下载入口。
- 已安装的 IQ2_XS 临时私有模型通过记忆与黑话共同写入／读回配置验证：仅纳入已确认的虚构记录，候选排除，基础模型未变，测试私有模型已删除。
- 当前可用内存／显存不足，新的真实生成、AI 黑话学习和召回试验未完成；本次配置写入验证不能证明这些能力的实际质量。此前 4K IQ2_XS 人格／记忆召回实测记录保留在 0.6.0 说明。
- Q4 / Q8 / BF16 只核对上游固定版本、文件大小、SHA-256 和文件头，未全量下载推理；32K、CPU、其他显卡及语音并发仍需相应硬件验收。
- 写入私有模型是系统提示持久化，仍占上下文，不是训练权重或保证记忆准确率。

English: 247 full regression tests passed, followed by 76 targeted checks after adding an API model-name collision guard. 2,025 MD/MDC recommendation combinations matched. Native bilingual UI checks passed. The installed IQ2_XS model accepted and returned approved synthetic memory and slang together in a temporary private default-prompt configuration; pending candidates were excluded and temporary models removed. New AI learning/generation/recall did not complete due to insufficient available RAM/VRAM. This verifies configuration persistence, not weight training or recall quality. Q4/Q8/BF16 and 32K/concurrent TTS have not received full local inference validation.

详见 [双需求条说明](docs/DUAL_SLIDERS.md)。

配套：[MD](https://github.com/reaiksp/ModelDock-Windows) · [MDC](https://github.com/reaiksp/ModelDock-Chat)

---

# ModelDock Chat 0.6.0 · 同参数大模型压缩实验

- 新增 Qwen3.8-27B 的 GSQ-RCO 四档低比特 GGUF，不通过换成小参数模型实现低配方案。
- 固定版本及校验后 Ollama CLI 导入文字 GGUF；实际加载成功才保存部署状态。
- 保留 4K / 16K、API、记忆、人格与本地语音预算；资源不足明确拒绝对应压缩方案。
- 默认关闭的短请求复述实验，至多一次且受上下文预算约束；原始文字不改写，不宣称可以恢复量化损失。
- 中文与英文界面、README 及精确来源同步更新。

详情：[模型来源与验证边界](docs/COMPRESSED_MODELS.md)。

配套：[MD](https://github.com/reaiksp/ModelDock-Windows) · [MDC](https://github.com/reaiksp/ModelDock-Chat)

## 本次验证 / Validation

- 189 项自动测试通过；打包后的独立程序启动与中英文界面检查通过。
- 已在本机 Ollama 0.40.13、RTX 5080 上完整下载并校验 IQ2_XS（8422841472 字节），通过本地 CLI 导入及 4096 上下文真实加载。
- 使用独立临时数据库的虚构记忆与人格验证通过：主聊天和私有快照均正确复述测试代号；短请求复述实验保留原文并得到算术结果。未使用真实用户聊天或启动语音，临时私有快照已删除，压缩基础模型保留供使用。
- IQ2_S / IQ3_XXS / IQ3_S 仅核对上游文件、GGUF 元数据和自动化兼容路径，未全量下载推理。16K、CPU、其他显卡及与 TTS 同时运行仍需硬件验收；并发语音检查目前是资源预算，不是性能实测。
- 真实短提示实验仅验证可运行与原文保留，没有证明重复可改善量化质量。

English: 189 automated tests and frozen startup checks passed. Full IQ2_XS download/hash/CLI import/4K inference passed on the local RTX 5080, including synthetic approved-memory, persona and private-snapshot recall. Other quantizations, 16K, CPU and concurrent TTS were not tested with full weights. Repetition is not a demonstrated quality improvement.

---

# ModelDock Chat 0.5.0 · 私有记忆模型实验 / Private memory experiment

本次仅更新独立的 [MDC](https://github.com/reaiksp/ModelDock-Chat)，继续兼容 [MD](https://github.com/reaiksp/ModelDock-Windows) 和既有本地语音路径。

- 有历史时在可用输入预算达到 90% 且有摘要空间的发送前自动概括、保存摘要检查点并建立关联续聊。原始消息与草稿保留，摘要不自动升级为已确认用户事实。
- “记忆与用户”新增私有模型快照：预览用户资料、已确认记忆、可选人格及当前续聊摘要，在预算内整条选取并显示遗漏数量。
- 仅在本机 Ollama 以新名称创建、校验默认系统提示；保留原模型及前一快照，创建失败不发布新版本。可删除 MDC 管理的快照。
- 可选择自动更新：续聊后，以及记忆、资料或人格变化后的下次本地发送时同步。API 聊天保留原提供商与应用内记忆，不创建云端模型。
- MDC 每轮仍使用数据库中的实时人格、记忆与摘要；快照默认提示供其他 Ollama 客户端使用，其他客户端自带系统提示时可能覆盖默认值。
- 个人快照不混入黑话词库导出。删除记忆记录不会清除旧聊天、摘要及旧模型中的副本，需要另行处理。

**实验边界：持久系统提示配置，不是修改权重、训练或微调；不突破模型上下文，也不保证模型完全遵循或准确回忆所有内容。** 本版不执行真实权重训练，不上传个人快照。详情见 [中英实验说明](docs/PERSONAL_MEMORY.md)。

English: Local private model snapshots persist approved memories, profile data, an optional persona and a continuation summary as an Ollama default system prompt. Automatic continuation checkpoints preserve original history. Optional refresh follows changes without changing API providers. This is not weight training; context limits and stale-copy deletion still apply.

## 本次验证 / Validation

- 131 项自动测试通过，覆盖既有聊天 / API / 语音兼容、自动概括与检查点、原文保留、候选隔离、预算整条取舍、外部删除重建、失败 / 取消清理及中英文界面。
- Ollama 0.40.13 / qwen3.5-9b:latest / 4096 上下文真实联调通过：默认 SYSTEM 记忆暗号复述、短人格、摘要 → 检查点 → 新快照，均使用独立临时合成数据。两个测试快照均已删除，基础模型 digest 未变。
- 打包后的 Windows 独立程序启动检查通过：六个页面、中英文、语音后端加载、记忆实验入口和默认关闭状态正常。
- 无真实权重训练，无新权重下载，无个人资料上传。模型效果验证仅覆盖这组简短合成资料，不代表任意长事实回忆准确率。
- 仍需分别验收干净 Windows、第三方 API 账户及不同本地模型；不把配置保存成功视为模型记忆效果保证。

---

# 0.4.0 容量模式与语音兼容更新

- 大上下文 / 大体量两种资源推荐。
- 为本地语音预留资源，保留 API 选择与原始用户资料。
- 按模型保存上下文，MDC 显式导入 MD 成功部署方案。
- 语音克隆与训练支持核对说明（本次未实现音频上传）。

配套项目：[MDC](https://github.com/reaiksp/ModelDock-Chat) · [MD](https://github.com/reaiksp/ModelDock-Windows)

# ModelDock Chat 0.3.0 · Windows x64 测试版

本次仅更新 MDC，MD 仓库不变。

- 独立的 Qwen3-TTS CustomVoice 本地部署：默认 1.7B，可选 0.6B / 自动搭配，独立 Python 3.12 环境和命令行安装。
- 同时展示不开语音 / 开语音的文字模型推荐；综合空闲显存、内存、模型与依赖磁盘余量及用途 / 速度 / 质量偏好。默认 CUDA 单卡索引 0，估算不保证速度。
- 本地随机令牌保护的回环服务；离线推理，停止 / 退出管理实例释放服务资源。
- 中文分段朗读、单条回复 / 选区入口、音色、语气、播放速度、暂停 / 停止、自动朗读与生成历史。
- 朗读副本与原始聊天分离，发音词典、语气和语言偏好独立保存；黑话可手工指定发音，不改权重。
- 客观音频检查及人工准确度 / 自然度 / 清晰度 1–10、概述与分析，评价与用户记忆分离。
- 中英文页面、README、许可说明和资源估算说明同步更新。

## 本次验证

- 73 项测试：原有聊天 / API / 记忆 / 黑话兼容性、联合资源预算、API 文字加本地语音、回环鉴权与关闭、分段取消、音频检查、评分、偏好保留及失败聊天不误读旧回复。
- 修复 Windows 播放器持有临时 WAV 导致退出清理出现 WinError 32 的问题，新增子进程启动 / 音频加载 / 退出回归检查。
- Qwen3-TTS 1.7B / PyTorch 2.11.0 CUDA 12.8 / RTX 5080 实际生成两段中文 PCM 音频，时长 3.84s 和 2.56s；首段包含首次加载共 66.61s，第二段生成 3.55s。未检出近静音或削波异常；这不是自然度或文字准确度的人工评分。
- 0.6B 完成接口 / 资源预算覆盖，尚未进行真实权重推理验收。当前不安装 FlashAttention，使用 PyTorch SDPA 路径。
- 使用 ModelScope 完成真实权重下载。首次直接下载 PyTorch 大包遇到网络内容不完整，校验拒绝安装；随后复用本机官方 wheel，在隔离环境完成验证。

仍为未签名测试版本；干净 Windows 环境及多种硬件仍需进一步验收。

---

# ModelDock Chat 0.2.0 · Windows x64 测试版

## 本次更新

- MD / MDC 均支持应用内中英文切换；MDC 保存分会话草稿，并将回复语言偏好与界面语言分离。用户原始输入、人格和记忆不被翻译改写。

- 主聊天支持多个手动配置的 OpenAI 兼容 API 供应商，提供 SSE 流式与普通 JSON 模式、输出预算参数和可选推理参数。
- 主聊天与黑话学习可分别选择供应商；黑话也可跟随主聊天，但始终使用独立学习请求，不带主聊天或个人记忆。
- 长期记忆扩展为评价 1–10（长期参考价值）、概述、分析；支持编辑、审核和按评分优先检索。
- 旧记忆数据自动迁移，保留原记录、证据与审批状态；旧学习 API 配置和 DPAPI 密钥继续兼容。
- 会话记录供应商关联，切换供应商时开启独立会话。
- 新增英文 README，并与中文互链；保留独立 SUB 声明、主项目链接与 Codex 署名。

## 验证

- 56 项自动测试通过，覆盖旧数据库迁移、供应商选择与密钥隔离、SSE 注释 / 多行 / Unicode / 取消、三维评分验证及学习数据隔离。
- 通过本机 Ollama 的真实兼容 API 完成聊天、三维记忆、黑话学习及检索回答联调。
- 未使用真实第三方 API 账户或额度验证供应商服务；支持的是 Chat Completions 兼容协议，不是所有供应商原生协议。
- 包仍是未签名测试版，需在干净 Windows 环境验收。API 模式下选定供应商会接收当前会话、人格、资料、已确认记忆与检索知识；详情见双语 README。

---

# ModelDock Chat 0.1.0 · Windows x64 测试版

独立 SUB 仓库：[ModelDock-Chat](https://github.com/reaiksp/ModelDock-Chat)。主项目：[ModelDock-Windows](https://github.com/reaiksp/ModelDock-Windows)。开发协作：OpenAI Codex。

## 已实现

- 图形化主聊天、流式回复、停止生成、会话持久保存、人格提示词与用户资料。
- 一键连接本机 Ollama，优先读取主项目选择的模型，双方源码、仓库和数据独立。
- 按模型上下文检查提示词长度，预留系统、记忆、知识、回复与安全空间；超限警告并保留输入。
- 长历史通过本机 AI 摘要并关联新续聊会话；长学习材料拆分到独立学习请求。
- 本地 AI 记忆候选、原文证据校验、用户确认 / 编辑 / 删除。
- 黑话学习台、仅供学习使用的独立 OpenAI 兼容 API 接入口、候选审核、本地词库检索与导入导出。
- 将人格和已确认词库保存成新 Ollama 派生模型；不修改原模型权重。
- Windows DPAPI 可选加密保存学习 API Key。

## 验证结果

- 31 项自动测试通过，覆盖预算、超限拦截、自动续聊、记忆证据、数据持久化、词库检索、外部 API 数据隔离、流式协议与阻塞取消、密钥保存及原生界面。
- 使用本机 `qwen3.5-9b:latest` 完成真实黑话学习、检索聊天和用户记忆分析；模型能按学习释义回答测试词。
- 真实创建包含词库的 Ollama 派生模型，读取其系统提示验证成功，并清理临时测试模型。
- 原生界面真实完成一键连接、模型上下文读取、流式回复和超长输入警告。
- 预览采用独立临时数据库与演示数据；不包含真实用户聊天。
- 打包后的独立 Windows EXE 启动检查通过，五个页面与独立数据库初始化正常。

## 当前限制

- 计数为 UTF-8 字节与模板开销的保守估算，不是特定模型的精确 tokenizer。摘要与 AI 提取的内容都可能不完整，记忆和黑话须经用户确认。
- 词库采用词语与别名匹配，不提供语义向量搜索或权重训练。派生模型是创建时的人格与词库快照。
- 外部提供商入口已通过可控 OpenAI 兼容 HTTP 服务验证；未使用真实第三方 API 账户和额度做联调。
- 尚未在无 Python 的干净 Windows 10 / Windows 11 系统上验收；驱动、内存容量和第三方提供商兼容性还需按实际环境测试。
- 便携包未签名，适合作为早期测试版分发。
