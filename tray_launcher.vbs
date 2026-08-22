' Launches tray_launcher.ps1 fully hidden and detached - sidesteps a
' cmd.exe `start` + `powershell -WindowStyle Hidden` combo that turned out
' to be unreliable (child process exits silently within seconds). WScript's
' Shell.Run uses a different process-creation path than cmd's `start`.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = scriptDir & "\tray_launcher.ps1"

Set shell = CreateObject("WScript.Shell")
cmd = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psScript & """"
shell.Run cmd, 0, False
