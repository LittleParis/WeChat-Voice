# 测试

Python 测试由合成 SQLite、SILK 和 MP3 构成，运行命令见 README。无需实际连接微信或提供私人数据。

## Android 隔离测试

先生成合成 ZIP，再构建测试 APK：

```powershell
& '.\.venv\Scripts\python.exe' scripts/make_mobile_test_fixtures.py
& '.\scripts\Build-LibraryApk.ps1' -WithTests
adb install -r build/voice-library-checks.apk
adb shell am instrument -w cn.voicekeep.library/cn.voicekeep.library.LibraryChecks
```

必须检查输出明确包含 PASS，不能只依据 am instrument 的退出码。LibraryChecks 使用独立临时存储，不清除用户语音库；测试音频是合成音频。

可选前台 UI 测试需要至少两条已导入语音，会操作界面并播放这些语音：

```powershell
adb shell am instrument -w cn.voicekeep.library/cn.voicekeep.library.LibraryUiChecks
```

测试完成后重新构建并安装不带 instrumentation 和夹具的产品 APK：

```powershell
& '.\scripts\Build-LibraryApk.ps1'
adb install -r build/voice-library-debug.apk
```

不要在不同的调试签名之间直接覆盖安装；本机开发签名位于被忽略的 build 目录。Android 测试覆盖去重、缺失/损坏补齐、坏包、事务回滚、日期与发送人筛选、真实音频播放及公共目录保存回读。
