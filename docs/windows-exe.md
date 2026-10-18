# Windows EXE 0.3.0

## 普通用户

从 GitHub Releases 下载单文件 EXE 后双击。程序显示一个小窗口并自动打开本机网页。小窗口提供打开网页、导出文件夹与退出按钮，网页也有“退出工具”。不需要 Python、Git、PowerShell 命令或外部依赖下载。

首次启动时选择微信聊天数据目录和账号，按网页提示完成密钥获取。已有有效密钥时复用；首次可能仍需从托盘退出并重新登录微信。这里没有实现微信登录授权读取聊天记录，也没有实现手机独立提取。

数据位置为 %LOCALAPPDATA%\VoiceKeep。私有连接、密钥和副本保存在其中的 .private，导出结果在 exports。EXE 的临时解包目录只保存程序资源，不用于持久化聊天数据。更新 EXE 后，原有用户数据保留。

关闭网页不等于退出后台。使用网页的退出按钮或小窗口退出，程序会停止自己的处理任务并释放端口，不会结束微信进程。重复双击同一用户数据目录的程序会打开已有实例，不启动第二套处理服务。

如果 8766 已被其他服务使用，程序会尝试其他本机端口，以启动窗口显示的实际地址为准。应用只监听 127.0.0.1。

本版本为 Windows x64 试用包，尚未做 Windows 代码签名。

## 构建

依赖固定于 requirements-build.txt，使用 PyInstaller 6.22.3 单文件 windowed 模式。源码运行依赖的固定版本下载方式见 README。

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-build.txt
& '.\scripts\Build-DesktopExe.ps1'
```

desktop.spec 只添加明确指定的网页、权限脚本、四份已固定版本的上游模块及其 MIT LICENSE，以及运行依赖和许可证；不打包工作区、用户数据目录或测试数据。PyInstaller 的资源目录与 sys.executable 在冻结模式下的含义见[官方运行时文档](https://pyinstaller.org/en/stable/runtime-information.html)。

冻结子进程通过同一个 EXE 的白名单 worker 模式运行，不把 EXE 当作 Python 解释器执行脚本。窗口模式下显式接回标准输出管道，父进程仍只识别允许的进度标记，不把原始输出返回网页。Windows 系统程序启动前临时恢复系统 DLL 搜索方式，参考[官方外部进程说明](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#launching-external-programs-from-the-frozen-application)。

## 验证

源码回归：

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests
```

打包后可在没有源码、PATH 不含 Python/Git 的目录中运行合成依赖检查：

```powershell
Start-Process -FilePath '.\VoiceKeep.exe' -ArgumentList '--self-test bundled-checks.json' -WindowStyle Hidden -Wait
Get-Content bundled-checks.json
```

检查包括 SILK 编解码、MP3 独立解码、Frida 本机运行库、合成 SQLCipher 认证解密、内置资源和冻结 worker 的输出管道。此检查不抓取微信密钥或读取私人聊天。

自动化验证可使用 --headless --no-browser --data-dir 测试目录。该模式用于测试，普通用户不需要输入参数。退出接口同其他写接口一样受本机会话和令牌保护。
