Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "D:\AGENT_AI\CivilHandbook"
WshShell.Run "pythonw run_web.py", 0, False
