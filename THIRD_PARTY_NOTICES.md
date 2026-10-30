# Third-party software

ModelDock Chat is an independent SUB of [ModelDock-Windows](https://github.com/reaiksp/ModelDock-Windows). Development assistance: OpenAI Codex.

| Component | Version | License and source |
|---|---|---|
| Python | 3.12 build runtime | PSF license; https://www.python.org/downloads/source/ |
| PySide6 / Shiboken6 | 6.8.3 | LGPL-3.0 / GPL / commercial alternatives; https://code.qt.io/pyside/pyside-setup.git/tree/?h=v6.8.3 |
| Qt Core / Gui / Widgets / Multimedia | 6.8.3 | LGPL-3.0 / GPL / commercial alternatives; https://download.qt.io/archive/qt/6.8/6.8.3/submodules/ |
| psutil | 7.0.0 | BSD-3-Clause; https://github.com/giampaolo/psutil |
| PyInstaller bootloader | 6.12.0 | GPL with bootloader exception; https://pyinstaller.org/en/v6.12.0/license.html |

The portable distribution includes license texts, installed distribution notices, application source and build instructions. Qt / PySide6 are dynamically linked in `_internal`; users may replace them with compatible modified versions and reverse engineer for debugging modifications to LGPL components. No modifications are made to Qt or PySide6. Commercial-license files in package metadata describe an alternative licensing option, not a commercial license granted by this application.

Ollama and model weights are not bundled. Ollama source and license: https://github.com/ollama/ollama. Model weights are subject to the terms of the selected model. The independent learning provider is selected by the user and subject to that provider's own terms.


## Optional local speech runtime

[Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (`qwen-tts` 0.1.1) and the official [1.7B](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) / [0.6B](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) CustomVoice model cards specify Apache-2.0. Weights are not included in this repository or portable package. The optional installer retrieves them from official Hugging Face / ModelScope repositories. PyTorch / torchaudio 2.11.0 CUDA 12.8 wheels, ModelScope 1.34.0 and other speech dependencies are installed separately, retain their own notices, and are subject to their respective licenses, including NVIDIA terms where applicable. Runtime inference uses local files with Hugging Face offline mode.

Qt Multimedia can use bundled FFmpeg libraries. See [Qt Multimedia third-party notices](https://doc.qt.io/qt-6/qtmultimedia-attribution-ffmpeg.html) and [FFmpeg licensing and source](https://ffmpeg.org/legal.html). Installed package license texts and application source accompany the portable distribution.
