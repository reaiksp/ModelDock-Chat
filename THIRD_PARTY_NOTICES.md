# Third-party software

ModelDock Chat is an independent SUB of [ModelDock-Windows](https://github.com/reaiksp/ModelDock-Windows). Development assistance: OpenAI Codex.

| Component | Version | License and source |
|---|---|---|
| Python | 3.12 build runtime | PSF license; https://www.python.org/downloads/source/ |
| PySide6 / Shiboken6 | 6.8.3 | LGPL-3.0 / GPL / commercial alternatives; https://code.qt.io/pyside/pyside-setup.git/tree/?h=v6.8.3 |
| Qt Core / Gui / Widgets | 6.8.3 | LGPL-3.0 / GPL / commercial alternatives; https://download.qt.io/archive/qt/6.8/6.8.3/submodules/ |
| PyInstaller bootloader | 6.12.0 | GPL with bootloader exception; https://pyinstaller.org/en/v6.12.0/license.html |

The portable distribution includes license texts, installed distribution notices, application source and build instructions. Qt / PySide6 are dynamically linked in `_internal`; users may replace them with compatible modified versions and reverse engineer for debugging modifications to LGPL components. No modifications are made to Qt or PySide6. Commercial-license files in package metadata describe an alternative licensing option, not a commercial license granted by this application.

Ollama and model weights are not bundled. Ollama source and license: https://github.com/ollama/ollama. Model weights are subject to the terms of the selected model. The independent learning provider is selected by the user and subject to that provider's own terms.
