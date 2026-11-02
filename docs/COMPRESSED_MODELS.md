# 同参数大模型压缩 / Same-model compression

本次以 Qwen3.8-27B 的 GSQ-RCO 作为可选低资源方案。保持同一 27B 参数级别；减少每个张量的存储精度，不是用更小参数模型替换。GSQ 学习量化网格与尺度；RCO 在精确大小预算下分配各张量精度。终端用户下载发布方已优化的 GGUF，不在本机重新执行优化或训练。

This update offers GSQ-RCO quantizations of the same Qwen3.8-27B base. Parameter class stays 27B. GSQ refines low-bit scalar quantization and RCO assigns tensor precision under an exact size budget. End users install the published GGUF, not an on-device compression training job.

## 固定来源 / Pinned source

Publisher: [ISTA-DASLab](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF). Revision: `d562806dbafae37109975e970aae91b43e73b440`. License: Apache-2.0 for these model weights. Each exact non-MTP filename is selected explicitly; no ambiguous short quantization tag is used.

| 档位 / Quant | 文件字节 / Bytes | 文件 GiB | SHA-256 |
| --- | ---: | ---: | --- |
| IQ2_XS | 8422841472 | 7.84 | `f0ae5006da0ce6225935339e4e989369f94de95d2263cf969519f8420c9ae02c` |
| IQ2_S | 9259510912 | 8.62 | `16c9802111aa9ef3acde465188d6d601f8db128ee3d828ad983a5caca4135ecb` |
| IQ3_XXS | 10094357632 | 9.40 | `fdfcb6a29b11188956dfbfd904223588a6c1b77eb250c3e8a36e1bd269df91f7` |
| IQ3_S | 11771546784 | 10.96 | `64b53b64c7aa39f20a7e54bd80582fe595b1d745624ee8a72e92508c0326d810` |

文件大小与运行显存不同。4K 上下文的运行估算依次为 10.0 / 10.8 / 11.5 / 13.0 GiB，16K 再预留 4 GiB；另扣桌面、系统和本地语音预算。它们是保守推荐值，不能保证特定显卡、驱动、速度或任务质量。没有可容纳档位时，会拒绝该压缩方案，不自动改成小参数模型。

File size is not runtime memory. Conservative 4K runtime estimates are 10.0 / 10.8 / 11.5 / 13.0 GiB respectively, plus 4 GiB for the 16K plan and separate desktop/system/local-speech reserves. These are estimates, not guarantees of speed, compatibility or quality. An insufficient budget does not trigger a tiny-model substitution.

## 下载、部署与兼容 / Installation

仅部署文字 GGUF，不下载视觉投影，也不提供图像输入。安装器从固定 revision 下载，支持核对范围的断点续传，检查完整文件长度和完整 SHA-256，再通过 Ollama CLI 的本地 `create` 导入。不会关闭 Ollama 的重定向地址安全检查。缓存与 Ollama 的模型副本可能同时占用双份空间，推荐会计入这部分磁盘余量。只有本机模型列表、上下文元数据和实际空提示加载均通过后，才保存部署成功状态。

The installer downloads only the text GGUF, supports exact-range resume, checks the complete file length and SHA-256, then imports through local Ollama CLI `create`. It does not disable Ollama redirect safety checks or install a vision projector. Cache and Ollama storage may temporarily or persistently occupy two copies; disk estimates include this. Local registration, context metadata and an actual empty-prompt load must pass before deployment is marked successful.

人格、用户资料、已确认记忆、未确认续聊摘要及黑话继续沿用 MDC 的原有边界。私有记忆模型直接引用已安装的量化模型，不再次量化或修改 GSQ-RCO 的分配。API 供应商不被自动切换。

MDC retains existing boundaries for personas, profiles, approved memories, unconfirmed summaries and vocabulary. Private memory snapshots reference the installed quantized base without quantizing it again. API providers are not switched automatically.

## 上下文与复述实验 / Context and repetition

低比特量化不等于上下文必须缩为 2K；仍提供 4K / 16K 资源方案，实际最大长度以模型元数据为准。MDC 接近输入预算上限时仍先概括并保存检查点，保留原文。

可选“短请求复述一次”默认关闭，仅本机、不超过 160 个 UTF-8 字节且预算足够时，在发送副本中复述当前请求一次。不会重复整段历史或人格，也不会修改原始输入。重复不能扩大上下文、保证回忆准确率或恢复量化损失；尚无本项目对这四档的效果改善结论。

Quantization does not itself require a 2K context. Existing 4K / 16K plans and model-metadata checks remain. The optional local-only repetition experiment is off by default, repeats at most one current request of 160 UTF-8 bytes or fewer, and skips the extra copy when it does not fit. It never rewrites stored text or repeats full history/personas. No improvement on these four quantizations has been established by this project.

## 验证边界 / Verification scope

已核对官方仓库文件长度、LFS SHA-256 与 Ollama manifest 的模型层摘要；使用有界 Range 请求解析四个 GGUF v3 文件头，确认 qwen35 架构与 262144 元数据上下文。这不等同于四档完整推理验收。原生 HF CLI 下载曾在本机代理的非公网地址检查处失败；固定下载/本地导入路径已完成 IQ2_XS 全量校验、CLI 导入、4K 实际加载及虚构记忆/人格回忆验证。其余三档和 16K 未完成权重推理验收，详见发布说明。

Publisher metadata and the Ollama manifest model-layer hashes were compared. Bounded Range reads parsed GGUF v3 headers (qwen35, metadata context 262144). Header checks do not establish full inference support for all variants. Native HF CLI pulling encountered a local proxy address-safety failure. The pinned-download/local-import path subsequently passed full IQ2_XS hashing, CLI import, 4K inference and synthetic memory/persona recall. The other variants and 16K were not validated with full weights; see the release notes.

## 参考 / References

- [GSQ implementation](https://github.com/IST-DASLab/GSQ)
- [RCO implementation](https://github.com/IST-DASLab/RCO)
- [Publisher model card and evaluations](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF)
- [HF exact-filename Ollama tags](https://huggingface.co/docs/hub/en/ollama#custom-quantization)

发布方基准成绩只代表其报告，不是本应用对所有中文任务或硬件的复现。
Publisher benchmark claims are their reported results, not this application's replication across languages or hardware.
