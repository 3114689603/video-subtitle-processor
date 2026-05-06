# Video Subtitle Processor - API模式启动指南

## 问题原因

你看到的错误是因为：**浏览器阻止了本地文件（file://）访问网络API（http://localhost）**

这是浏览器的安全策略，无法直接通过双击打开 HTML 文件使用 API 模式。

---

## 正确启动方式（3个步骤）

### 方法一：一键启动（推荐）

**双击运行 `start-all.bat`**

这会同时启动：
1. 后端 API (http://localhost:5000)
2. 前端服务器 (http://localhost:8080)

然后浏览器访问：**http://localhost:8080**

✅ 右下角显示 "API已连接" 即成功！

---

### 方法二：手动启动

**步骤1：启动后端**
```bash
cd Desktop\视频字幕处理器
python app.py
```
保持窗口运行！

**步骤2：启动前端服务器（新 CMD 窗口）**
```bash
cd Desktop\视频字幕处理器
python -m http.server 8080
```

**步骤3：浏览器访问**
```
http://localhost:8080
```

---

### 方法三：VS Code（开发者推荐）

1. 用 VS Code 打开项目文件夹
2. 安装 "Live Server" 插件
3. 右键点击 index.html → "Open with Live Server"

---

## 为什么不能直接双击打开？

| 打开方式 | URL 协议 | 能否访问 API | 状态显示 |
|---------|---------|------------|---------|
| 双击 HTML | `file://` | ❌ 被浏览器阻止 | 离线模式 |
| 本地服务器 | `http://` | ✅ 可以访问 | API已连接 |

浏览器安全策略：`file://` 页面不能访问 `http://localhost`

---

## 验证是否成功

### 1. 后端启动成功
CMD 显示：
```
Mode: Whisper
URL: http://localhost:5000
 * Running on http://127.0.0.1:5000
```

### 2. 前端连接成功
浏览器右下角显示：
- 🟢 **API已连接** ← 成功！

### 3. 测试 API
浏览器访问：http://localhost:5000/api/health
应该返回 JSON 数据。

---

## 常见问题

### Q: 提示 "找不到模块 whisper"？

A: 正常！我之前修复了代码，现在没有 whisper 也能运行。
但你说需要真实语音识别，需要：

```bash
python -m pip install openai-whisper
```

### Q: 端口 5000 被占用？

A: 修改 app.py 最后一行：
```python
app.run(host="0.0.0.0", port=5001, debug=True)  # 改为5001
```

然后修改 app.js：
```javascript
const API_BASE_URL = 'http://localhost:5001/api';
```

### Q: 如何确认是 API 模式？

A: 看右下角状态：
- 🟢 **API已连接** = API 模式（真实处理）
- 🟡 **离线模式** = 本地演示（模拟数据）

---

## 快速检查清单

- [ ] 运行 `start-all.bat`
- [ ] 看到两个 CMD 窗口（一个 API，一个 HTTP 服务器）
- [ ] 浏览器访问 `http://localhost:8080`（不是双击 HTML）
- [ ] 右下角显示 "API已连接"
- [ ] 上传视频测试

---

**现在双击 `start-all.bat` 试试看！**
