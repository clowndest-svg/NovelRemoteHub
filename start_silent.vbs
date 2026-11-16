Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = "D:\RuanJian\NovelRemoteHub"
ws.Run """C:\Python314\pythonw.exe"" ""D:\RuanJian\NovelRemoteHub\local_bridge.py""", 0, False
