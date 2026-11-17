Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = "D:\RuanJian\NovelRemoteHub"
ws.Run """C:\Python314\pythonw.exe"" ""D:\RuanJian\NovelRemoteHub\launch_daemon.py"" --supervise", 0, False
