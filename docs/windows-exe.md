# Qt 原生桌面版 0.4.0

普通用户下载 Windows EXE 后双击，直接进入 Qt 程序窗口。所有目录连接、会话选择、发送人和日期筛选、试听、保存 MP3、批量导出与手机数据包生成均在窗口内操作，不会自动打开浏览器。无需安装 Qt、Python 或 Git。

## 使用

1. 点击右上方“连接本机微信”，选择“自动查找”，或使用“选择文件夹…”打开系统目录选择器。
2. 检查目录并选择账号，点击“连接并读取语音”；首次可能需要按提示从微信托盘退出、手动重新打开并登录。
3. 左侧选择会话，右侧按发送人、日期和时间顺序筛选。日期勾选后才生效，范围包含起止当天。
4. 双击语音或点击底部“试听”。支持暂停、继续、进度跳转、音量、下一条和连续播放。
5. “保存所选 MP3…”使用系统保存窗口；“导出当前 N 条”覆盖全部筛选结果；“生成手机数据包”生成供 Android App 导入的 ZIP。批量完成后可打开结果文件夹。
6. 关闭窗口退出。正在处理时先停止自己的任务，不结束微信。重复双击程序会唤醒已有窗口。

用户数据继续保存在 %LOCALAPPDATA%\VoiceKeep，与 0.3.0 共用同一成功配置；请先退出旧版再打开 Qt 版。旧版 0.3.0 的启动网页方式不再是默认界面。更新不会清除已准备的数据。当前仍需电脑取得原始语音，不支持未 Root 手机独立读取微信数据。

## 实现与构建

PySide6 6.10.2，Qt Widgets 原生控件、Qt Multimedia 播放器，不使用 WebEngine 或内嵌网页。桌面窗口直接调用本机业务对象；内部 loopback 服务用于兼容已有 Web 工具及单实例唤醒，普通用户无需访问网址。连接与音频准备在后台线程执行，Qt 主线程只更新界面。

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r requirements-build.txt
& '.\scripts\Build-DesktopExe.ps1'
```

开发时可直接运行 scripts/desktop_app.py。原来的 scripts/Start-VoiceLibrary.ps1 保留为可选 Web 入口。

desktop.spec 显式包含所需依赖、平台插件、媒体后端及许可证，不包含用户数据。Qt/Python 的 MSVC 运行库需在包根目录使用兼容的较新 Qt 版本，避免较旧运行库先被加载。资源从冻结目录读取，持久化数据保存在用户数据目录；窗口关闭后安全停止后台。

构建脚本限制 DLL 搜索 PATH，避免开发工具的同名 DLL 混入发布包。Qt 使用 Windows 自带的 ICU，包内不包含第三方工具的 `icuuc.dll`。目标系统为 Windows 10 / 11 x64。

## 验证

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests
```

59 项源码测试包含 8 项 Qt 原生测试：发送人、日期、排序、导出范围、MP3 保存、播放/暂停/连播、原生唤醒及渲染。Qt 原生测试使用合成数据，播放器静音，避免播放私人内容。

打包后可在无源码且 PATH 不含 Python/Git 的目录中测试：

```powershell
Start-Process -FilePath '.\VoiceKeep.exe' -ArgumentList '--headless --self-test core-check.json' -WindowStyle Hidden -Wait
Start-Process -FilePath '.\VoiceKeep.exe' -ArgumentList '--headless --qt-check qt-check.json --data-dir qt-test-data' -WindowStyle Hidden -Wait
```

core-check 验证原始音频与加密依赖、冻结 worker；qt-check 使用 offscreen Qt 平台验证实际原生窗口、目录控件、播放器、单实例唤醒、截图和退出。给定已连接的测试数据目录时，还验证真实语音在 Qt 中播放。原生窗口截图来自 QWidget 自身，不包含其他程序画面。

本版本是 Windows x64 未签名试用包。Qt 绑定及媒体接口见 [Qt for Python 官方文档](https://doc.qt.io/qtforpython-6/)。
