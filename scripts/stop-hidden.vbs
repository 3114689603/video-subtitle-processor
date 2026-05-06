' 视频字幕处理器 - 后台停止脚本
' 双击运行，关闭所有相关后台服务

Set WshShell = CreateObject("WScript.Shell")
Set objWMIService = GetObject("winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")

' 查找并结束 Python 进程
Set colProcesses = objWMIService.ExecQuery("Select * from Win32_Process Where Name = 'python.exe'")

count = 0
For Each objProcess in colProcesses
    ' 检查是否是字幕处理器的进程（通过命令行参数判断）
    cmdLine = objProcess.CommandLine
    If InStr(cmdLine, "app.py") > 0 Or InStr(cmdLine, "http.server 8080") > 0 Then
        objProcess.Terminate()
        count = count + 1
    End If
Next

If count > 0 Then
    MsgBox "已停止 " & count & " 个服务进程", 64, "停止成功"
Else
    MsgBox "没有运行中的服务", 64, "提示"
End If
