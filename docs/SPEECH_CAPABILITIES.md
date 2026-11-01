# 语音：个人录音、克隆与训练 / Speech capability audit

核对日期：2026-11-01。

| 类型 | 能否使用个人音频 | 当前软件边界 |
|---|---|---|
| 本地 Qwen3-TTS CustomVoice 0.6B / 1.7B | 面向预设音色；不能把任意录音直接传给当前 CustomVoice 朗读流程 | MDC 目前安装的是 CustomVoice，未提供录音训练按钮 |
| 本地 Qwen3-TTS Base 0.6B / 1.7B | 官方支持参考录音声音克隆；官方提供单说话人微调流程 | 需要 Base 模型、参考 WAV；微调还需音频和对应转写 JSONL。推理预算不能当作训练显存预算 |
| 硅基流动特定 TTS 模型 | 支持音频上传创建音色，由后续合成使用 | 服务端音色复刻，不等同通用微调；具体模型、材料格式和权限依官方接口 |
| 百炼支持复刻的 TTS 模型 | 可使用参考录音创建音色 | 需要对应的声音注册/复刻接口及匹配的目标模型，通用聊天 API 无此能力 |
| Azure Personal Voice | 支持个人声音样本与同意录音 | 需要相应功能权限及官方同意流程；普通 SSML 合成接口不负责训练 |

**以上为能力核对，不代表 MDC 已实现上传、克隆或训练流程。当前 MDC 的 API 供应商管理用于文字聊天；内置朗读仍为本地 CustomVoice。** 不会在后台上传个人音频。若后续实现上传，应在用户选择对应服务并确认素材用途后进行；仅使用本人或已获授权的录音。

官方依据 / Official references:
- [Qwen3-TTS: voice clone and CustomVoice](https://github.com/QwenLM/Qwen3-TTS)
- [Qwen3-TTS Base single-speaker fine-tuning](https://github.com/QwenLM/Qwen3-TTS/tree/main/finetuning)
- [SiliconFlow upload voice](https://api-docs.siliconflow.cn/docs/api/uploads-audio-voice-post)
- [Alibaba voice cloning and voice design](https://docs.bailian.console.aliyun.com/zh/model-studio/voice-cloning-and-voice-design)
- [Azure personal voice consent](https://learn.microsoft.com/en-gb/azure/ai-services/speech-service/personal-voice-create-consent)

English: CustomVoice is the currently integrated preset-voice engine. Base supports reference-audio cloning and official single-speaker SFT with audio/transcript data. Some cloud providers offer voice enrollment, which is distinct from general fine-tuning. These upload/training routes are audited only and are not integrated into MDC. Training requires separate resource evaluation; no personal audio is uploaded by this update.
