Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

Dim pythonPath
pythonPath = "C:\Program Files\Python312\pythonw.exe"

If Not fso.FileExists(pythonPath) Then
    pythonPath = "pythonw.exe"
End If

WshShell.CurrentDirectory = "D:\AGENT_AI\CivilHandbook"
WshShell.Run """" & pythonPath & """ run_web.py", 0, False
