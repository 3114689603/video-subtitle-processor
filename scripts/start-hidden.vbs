' 视频字幕处理器 - 后台启动脚本
' 双击运行，服务在后台启动，不显示黑窗口

Set WshShell = CreateObject("WScript.Shell")

' 获取当前目录
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' 启动后端（后台）
WshShell.Run "cmd /c cd /d """ & strPath & """ && python app.py""", 0, False
WScript.Sleep 3000

' 启动前端（后台）
WshShell.Run "cmd /c cd /d """ & strPath & """ && python -m http.server 8080""", 0, False
WScript.Sleep 2000

' 打开浏览器
WshShell.Run "http://localhost:8080"

MsgBox "视频字幕处理器已启动！" & vbCrLf & vbCrLf & "访问: http://localhost:8080" & vbCrLf & vbCrLf & "关闭方式：任务管理器结束 python.exe", 64, "启动成功"
