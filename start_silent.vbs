Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = "D:\RuanJian\NovelRemoteHub"
ws.Run "pythonw.exe ""D:\RuanJian\NovelRemoteHub\local_bridge.py""", 0, False
