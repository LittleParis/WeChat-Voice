# 第三方依赖

## 本地数据库参考实现

- 来源：https://github.com/freanocank/wechat-decrypt
- 固定提交：f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd
- 许可证：MIT，参见该固定版本的 LICENSE。
- 源码 checkout 不包含其源码；开发者按 README 下载到忽略的 third_party/wechat-decrypt。Windows EXE 内置该版本的 extract_raw_key.py、decrypt_all.py、crypto_backend.py、wal_snapshot.py 和 MIT LICENSE。
- 运行时复用 Windows 捕获函数、SQLCipher4 认证解密和 WAL 快照代码；不运行上游安装器、强制关闭微信的入口、MCP 注册或转写服务。
- 本项目在保存密钥前额外认证数据库第一页，逐库生成副本并执行 SQLite quick_check。

## Python 依赖

版本固定于 requirements.txt：

- frida 17.2.17：本地进程启动阶段获取数据库密钥。
- pycryptodome 4.0.1：加密与认证依赖。
- pilk 0.2.4：SILK 解码，包声明 GPL-3.0，https://github.com/foyoux/pilk 。
- lameenc 1.9.0：LAME MP3 编码绑定，https://github.com/chrisstaite/lameenc 。
- miniaudio 1.71：独立音频解码验证，https://github.com/irmen/pyminiaudio 。
- pytest 9.1.1：合成回归测试。

各依赖的授权以其对应版本的许可证文件为准；Windows EXE 携带运行依赖与可取得的许可证文件，源代码入口为上述项目及本仓库，构建工具为 PyInstaller 6.22.3。Android App 使用系统 MediaPlayer 播放电脑端生成的 MP3，不包含此前录制实验或 Java LAME 编码器。

## Qt 原生桌面界面

- PySide6 / Qt for Python 6.10.2，来源：https://pypi.org/project/PySide6/6.10.2/ 。
- 配套 PySide6_Essentials、PySide6_Addons、Shiboken6 均固定为 6.10.2；分发包保留相应许可证与声明。Qt for Python 使用相应版本所附的 LGPL/GPL 或商业许可条款，见官方项目说明与包内 LICENSES。
- 窗口使用 Qt Widgets 和 Qt Multimedia，不使用 Qt WebEngine；Windows 中文字体从系统字体目录加载，不随应用再分发系统字体。
