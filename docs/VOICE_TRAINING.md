# Batch voice training / 批量语音训练

MDC is a separate SUB of [ModelDock Windows](https://github.com/reaiksp/ModelDock-Windows).
This experimental page runs locally. It never uploads your recordings or includes
them, trained checkpoints, or personal configuration in Git.

## 中文使用方法

1. 打开“中文朗读与联合部署 → 多音频训练”。每个项目只使用同一个人的录音。
2. 点击“批量添加音频”，可多选 WAV / FLAC / MP3 / OGG / M4A；实际解码取决于训练环境。
   每段建议 2–30 秒，避免长篇整段录音、音乐和多人混说。
3. 在每行“对应文本”中填写录音里真正说出的内容，可以逐条试听。
   同目录的 `001.wav` 和 UTF-8 `001.txt` 会自动配对，不把文件名当转写。
   也可导入 UTF-8 CSV，表头 `audio,text`，例如：

   ```csv
   audio,text
   001.wav,你好，这是第一段录音。
   002.wav,"第二段，有标点也可以。"
   ```

   使用完整文件名，含扩展名。不同目录同名、重复 CSV 文件名和未选择的音频会报错。
   缺少文本时不会进入训练；应用不会猜测录音内容。
4. 指定项目名和参考音频行号；参考建议 10–30 秒，至少 2 秒，同一训练集共享参考。
5. 点击“检查并保存训练数据”。应用归一化音频并自动生成官方 JSONL。
   成功后表格清空，片段已保存到项目中；可继续添加下一批。
   失败会撤销本批次，不删除先前的片段和参考录音。原始录音不变。
6. 首次训练需要指定已准备好的 Python、官方 finetuning 脚本目录、**Base** 模型目录。
   选择 0.6b / 1.7b 必须与目录内配置相符；CustomVoice / VoiceDesign 不能作为此训练入口的底模。
   这些工具路径只在主动开始训练时保存，不修改聊天供应商、人格或用户提示词。
7. 停止语音服务、占显存的文字模型和自用 K 服务，再点击“开始训练”。
   默认 batch=1、3 轮，训练仍可能显存不足；依赖与权重不会偷偷下载。
8. 日志和状态显示在页面下方；可停止训练、打开项目目录，完成后输入测试文本试听。
   原有 K 控制台的 checkpoint 部署保持独立；此页面不自动改写 K 网关或主聊天配置。

项目保存在当前 MDC 数据目录的 `speech/training/<项目名>`，完整录音、文本、日志与输出均留在本地。
后台训练启动后可在项目日志查看进度；关闭应用不会自动删除训练输出。
当前只支持官方单说话人 SFT，不是多说话人训练，也不自动完成长录音切句与识别。

## English quick start

Open **Chinese readout and joint deployment → Multi-audio training**. Add multiple
clips from one speaker, then edit the exact transcript beside each file. Matching
UTF-8 `.txt` sidecars are read automatically. Alternatively, import UTF-8 CSV with
`audio,text` headers and exact filenames including extensions. You can audition
each row. Missing transcripts and ambiguous or duplicate names are rejected.

Choose a project and a reference row (at least 2 seconds, preferably 10–30).
**Check and save training data** converts the recordings and writes official
JSONL automatically. On success, the editor clears and the clips remain in the
project. On conversion failure, the current batch is rolled back. Previous data
and original recordings are preserved.

Before starting training, select an existing training Python environment, the
official finetuning directory, and a matching **0.6B-Base or 1.7B-Base** directory.
Install the upstream training dependencies separately. No model is downloaded
implicitly. Stop readout, other GPU models, and any private K voice service first.
The default is batch size 1 and 3 epochs; hardware feasibility is not guaranteed.
Monitor logs, stop the job, open its folder, and audition the resulting checkpoint
from this page. It does not automatically replace the chat model or K gateway.

### Tone instructions

**1.7B CustomVoice** accepts natural language instructions such as
“Speak gently and slowly.” Select it in deployment, then enter a personal style
or choose an example in readout preferences. Switching examples is explicit and
never rewrites the user's chat text or persona. **0.6B CustomVoice and Base voice
cloning do not support this instruction channel.** A Base-finetuned checkpoint
does not thereby acquire reliable instruction-controlled emotion.

### Upstream prerequisites and limits

- [Official Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS)
- [Official single-speaker fine-tuning guide](https://github.com/QwenLM/Qwen3-TTS/tree/main/finetuning)
- The upstream input fields are `audio`, `text`, `ref_audio`; all clips share a reference.
- Supported scripts: `prepare_data.py` and `sft_12hz.py`, or a user-supplied Windows
  adapter `sft_12hz_win.py`. Windows-specific gradient flags are sent only to the adapter.
- This feature adds a GUI and orchestration; it does not claim successful training
  on every GPU, tested emotion quality, or completed training of the user's voice.

### Gemma 3 12B IT QAT

The deployment purpose selector includes **Gemma 3 · 12B IT QAT**, tag
`gemma3:12b-it-qat`. It uses the same conservative context and speech resource
budgeting as other text candidates. Only an explicit download action runs
`ollama pull`; opening this page or updating source does not alter installed models.
The published model is approximately 8.9 GB; catalog estimates are 8.4 GiB download
and 12 GiB runtime at 4K, plus the existing context and speech reserves. These are
estimates, not benchmarks. MDC's current chat input remains text-only.

Sources: [Ollama model card](https://ollama.com/library/gemma3:12b-it-qat),
[Gemma terms](https://ai.google.dev/gemma/terms).

Developed with OpenAI Codex. MDC uses the independently developed
[Ollama CLI](https://ollama.com/) and is not an official Ollama product.
