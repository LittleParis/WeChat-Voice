# WeChat Voice · 语音档案

从本人授权使用的 Windows 微信本地数据库提取原始语音，转换为 MP3，并按会话、发送人和时间整理。提供本机 Web 工具与 Android 离线语音档案 App。

**当前需要电脑辅助提取。** Android App 导入电脑生成的数据包后可离线筛选、试听与保存；尚未实现未 Root 手机独立读取微信原始数据。提取过程不要求播放消息，不使用录音，不上传聊天数据。

## 功能

- 网页自动查找或手动配置微信聊天数据目录，选择账号；获取或复用已认证的密钥，查看进度、取消、刷新数据。
- 按群聊 / 联系人浏览，按发送人、日期筛选与时间排序，按需试听和下载 MP3。
- 导出当前筛选结果，或生成包含 MP3、会话、发送人、时间与 SHA256 的手机 ZIP 数据包。
- Android 导入 ZIP、增量去重、补齐缺失或损坏音频、导入记录、前台连续播放、多选保存与单条分享。

## 环境与限制

电脑端使用 Windows、Python 3.10+ 和 Git。已在 Windows 微信 4.1.13.65 的 db_storage / SQLCipher4 数据结构上验证；其他微信版本需要实际确认兼容性。

手机端最低 Android 10（API 29），已在 Android 16 真机验证。App 版本 0.2.0。当前不提供微信账号 OAuth 登录，不支持新式 RMFH 官方备份容器解密，不保证电脑尚未接收的手机媒体能够导出。

## 安装并启动 Web

```powershell
git clone https://github.com/LittleParis/WeChat-Voice.git
Set-Location WeChat-Voice
python -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt

# 取得固定版本的外部依赖；仅下载源码，不运行其安装器。
git clone https://github.com/freanocank/wechat-decrypt.git third_party/wechat-decrypt
git -C third_party/wechat-decrypt checkout f0b3f9f95dda2d954ee3dbe1d139fb1944a5ecfd

& '.\scripts\Start-VoiceLibrary.ps1'
```

终端保持运行，浏览器打开 <http://127.0.0.1:8766/>。服务仅监听本机地址。

1. 点击“连接本机微信”，自动查找或填写微信设置中的聊天数据保存目录。支持 xwechat_files 根目录、账号目录或 db_storage，安装目录和 Backup 不适用。
2. 选择账号，点击“连接并读取语音”。已有密钥验证有效时直接复用；首次获取可能需要按页面提示从托盘退出微信，再手动打开并登录。程序不会自动关闭微信，获取等待上限 5 分钟。
3. 数据准备成功后收起设置，选择会话并筛选、试听或导出。电脑新增聊天后点击顶部“刷新聊天数据”，无需重启网页服务。
4. 若目标语音仅保存在手机上，需要先使用微信官方“导出到电脑”让同账号电脑微信接收，再刷新工具；官方备份容器不等于可直接解析的聊天数据库。

密钥不需要复制到网页，也不会返回浏览器。连接配置和解密副本保存在忽略的 .private 目录。每次刷新生成独立副本，失败或取消保留原来的列表；旧副本暂不自动清理，会占用额外磁盘空间。

## Android 使用与构建

Web 选择会话和筛选范围 → “生成手机数据包” → 将 ZIP 传到手机 → 在“语音档案”App 中直接导入，无需解压。

导入后可离线按会话、发送人和日期查找、试听、连续播放或多选保存。离开 App 暂停播放；保存位置为 Music/VoiceKeep/会话目录。相同文件重复保存时跳过。App 不申请网络、录音或所有文件访问权限。

安装 JDK 17、Android SDK Platform 34 与 Build Tools 34.0.0，设置 JAVA_HOME 和 ANDROID_SDK_ROOT 后运行：

```powershell
& '.\scripts\Build-LibraryApk.ps1'
```

也可显式传入 -JdkPath 和 -SdkPath。结果为 build/voice-library-debug.apk，使用本机生成的开发签名，签名文件不会提交到 Git。

## 验证

```powershell
& '.\.venv\Scripts\python.exe' -m pytest -q tests/test_original_voices.py tests/test_voice_library.py tests/test_mobile_package.py tests/test_desktop_connection.py
```

当前 43 项 Python 回归通过，覆盖原始音频关联、真实合成音频编解码、稳定消息标识、筛选、HTTP 会话校验、手机数据包、连接失败保留和取消等。测试使用合成数据，不附带私人聊天样本。

Android 隔离测试覆盖非法包拒绝、事务回滚、去重、音频修复、筛选、MediaStore 保存回读；真实 UI 验证前台播放、暂停、连续切换和离开暂停。测试构建方法见 [测试说明](docs/testing.md)。首次密钥获取的网页编排使用合成测试；不能据此承诺每个微信版本均能获取密钥。

## 项目结构

- scripts/：本地连接、密钥认证、数据副本、原始语音转换、Web 服务与 APK 构建。
- web/：本机浏览器界面。
- android-library/：Android 离线档案 App。
- tests/：Python 合成回归与 Android instrumentation。
- docs/：连接流程与测试说明。

源码不包含微信数据库、密钥、账号配置、真实语音、个人操作日志、截图或签名文件。第三方依赖的固定版本与许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。
