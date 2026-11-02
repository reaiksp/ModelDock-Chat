# 双需求条 / Two recommendation sliders

MD 与 MDC 独立运行，使用一致的两条需求设置。拖动只计算推荐、保存设置；用户主动点击后才下载。不会因拖动变更当前聊天模型、API 供应商或重写用户资料。

## 1. 精简程度 / Compression

| 档位 | 同一 Qwen3.8-27B 文字权重 | 下载约 GiB | 4K 运行估算 GiB |
| --- | --- | ---: | ---: |
| 1 | GSQ-RCO IQ2_XS | 7.9 | 10.0 |
| 2 | GSQ-RCO IQ3_XXS | 9.5 | 11.5 |
| 3 | Q4_K_M | 17.7 | 20.5 |
| 4 | Q8_0 | 26.7 | 30.0 |
| 5 | BF16 完全体文字权重 | 50.2 | 54.0 |

‘完全体’仅指 BF16 文字权重，不包括单独视觉投影或图像能力。档位按压缩程度排列，不是准确率或速度分数。原 IQ2_S / IQ3_S 仍在手动目录。普通聊天／代码目录当前没有为每个家族提供五种精度，因此精度条只在‘压缩大模型’用途启用。

## 2. 对话长短与记忆需求 / Conversation and memory requirement

| 档位 | 需求 | 上下文 | 相对4K额外内存估算 GiB |
| --- | --- | ---: | ---: |
| 1 | 短聊、基础记忆 | 2048 | 0 |
| 2 | 日常对话、常规记忆 | 4096 | 0 |
| 3 | 较长对话、加强检索 | 6144 | 0.75 |
| 4 | 长聊、详细续聊摘要 | 8192 | 1.5 |
| 5 | 很长对话、更重视保留细节 | 10240 | 2.125 |

MDC 随档位增加已确认记忆与黑话的可选检索预算、续聊摘要预算，并更早预留概括空间。实际请求仍检查模型元数据及总输入预算。人格、用户资料、原始输入不为适配而截断；完整已存聊天不会因档位低而删除。可选条目只在预算内整条纳入，未确认摘要不升级为用户事实。检索和摘要仍可能遗漏或出错，需求档位不是记忆准确率承诺。

## 联合推荐与音频 / Joint resource policy

先预留桌面、系统、上下文和本地语音，再检查模型文件与双份缓存磁盘空间。GPU、RAM或多张卡的显存不会相加。只在同一模型家族中，从不高于目标的可行精度里推荐最高档；降档明确显示。对话需求不静默缩短；最低精度仍不满足就不给出方案。

明确指定0.6B或1.7B语音时尊重选择。MDC自动音频先保持尽可能高的文字档位，再在同一文字档位选择音频；质量偏好倾向1.7B，否则倾向0.6B。API文字不受本地精度条控制，本机仅估算语音；不会修改供应商声明的上下文。

MD成功部署的方案记录目标档、实际档、对话需求、上下文和音频。MDC显式导入后兼容使用。旧方案没有新字段时按原上下文恢复需求，原始用户偏好及旧字段保留。CPU大型模型仍要求质量优先并可能很慢。

## 记忆和黑话写入 / Memory and slang persistence

量化模型可以使用MDC实时检索、学习／分析候选以及私有模型默认提示。候选需确认；私有快照可同时包含已确认记忆、黑话、人格及续聊摘要。源权重保持发布方内容，保存系统提示不等于训练权重，仍占上下文。删除原记录不会删除旧聊天、摘要或旧模型快照里的副本。

只有成功部署或显式MD导入后才按该模型启用所选记忆策略；拖动推荐条不更改正在使用的模型策略。

## 来源与验证 / Sources and validation

- [GSQ-RCO 低比特发布](https://huggingface.co/ISTA-DASLab/Qwen3.8-27B-GSQ-RCO-GGUF)，固定版本 d562806dbafae37109975e970aae91b43e73b440。
- [ggml-org Q4 / Q8 / BF16](https://huggingface.co/ggml-org/Qwen3.8-27B-GGUF)，固定版本 a316ea527fde32c12487be7574e2db5ac2ad2a7e。
- 所有下载采用精确非MTP文件名、长度和SHA-256校验，不在用户机运行优化训练；Q4 / Q8 / BF16已核对文件头，尚未在本项目全量推理。
- MD / MDC 已通过 2,025 组硬件、精度、对话与语音组合的推荐一致性检查。
- IQ2_XS 4K曾实测加载、人格和记忆；本次实际创建临时私有模型并读回已确认的虚构记忆与黑话配置，候选未写入，测试模型已清理。当前资源不足导致新的生成／黑话学习／召回试验未完成；配置持久化不代表召回准确率。

## English

The first five-step slider selects compression of the same 27B base, from IQ2_XS through IQ3_XXS, Q4_K_M and Q8_0 to real BF16 text weights. It is not a quality score. The second selects conversation and memory requirements: 2K, 4K, 6K, 8K or 10K with larger retrieval/summary budgets and earlier summarization. Full stored history and original user text remain intact. Memory accuracy is not guaranteed.

Both requirements are evaluated with actual hardware, disk and speech reserves. A recommendation never exceeds the requested precision; any fallback is shown. Conversation length is not silently shortened. API text is not controlled by local precision settings. Explicit speech selection is respected; automatic speech first maximizes feasible text precision, then chooses speech within that text grade.

Dragging neither downloads weights nor changes the active chat backend. Download requires a deliberate click. Successful deployment or explicit MD import applies a per-model memory strategy. Approved memories and slang can be saved together in a private default-prompt snapshot; pending data is excluded. This stores configuration, not trained weights, and still consumes context.

Precision files are pinned to the above publishers and revisions with exact size/SHA-256 checks. Q4/Q8/BF16 received metadata/header checks, not full local inference validation. Across 2,025 hardware/precision/context/voice combinations MD and MDC returned matching recommendations. The installed IQ2_XS model passed a temporary private-configuration write/read check for approved synthetic memory and slang, excluding candidates; test models were removed. New learning/generation/recall could not complete with the available resources. Configuration persistence does not establish recall accuracy. See release notes for details.


## 2K一档更新 / 2K increments

五个推荐档固定为2048、4096、6144、8192、10240。旧偏好只迁移推荐位置并显示新值，超过新条范围的偏好映射到最高档；已部署模型、聊天草稿、人格、API配置以及已保存方案不自动改上下文。旧16K/32K方案仍可导入并继续使用原值；新2K/6K/10K方案可精确导入，不向下取整为4K/8K。

内存预留仍以4K模型估算为基准；2K不虚构固定内存节省，6K/10K在现有锚点间线性估算。新参数家族保留更高的上下文预留系数。语音模型的固定显存、系统内存和磁盘预留不随文字上下文降低；2K不是语音一定可运行的保证。人格、原始输入与语言适配仍检查实际预算，超出只警告，不截断原文。

English: Five recommendation positions now represent2K/4K/6K/8K/10K. Old preferences migrate once; active models and saved plans keep their exact context. Older16K/32K plans remain usable, and new2K/6K/10K plans import without rounding down. Resource estimates retain a4K baseline and interpolate additional context reserves. Speech memory requirements are independent;2K does not guarantee feasibility. Original user text is preserved and oversized requests are warned about.
