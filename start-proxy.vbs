Set oShell = CreateObject("WScript.Shell")
Set fso    = CreateObject("Scripting.FileSystemObject")
proxyDir   = fso.GetParentFolderName(WScript.ScriptFullName)
oShell.Run "C:\Python314\python.exe """ & proxyDir & "\smart-proxy.py""", 0, False
