' Launches tray_launcher.ps1 (this folder) fully hidden and detached - see
' ../tray_launcher.vbs for why Shell.Run is used instead of cmd's `start`.
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = scriptDir & "\tray_launcher.ps1"

Set shell = CreateObject("WScript.Shell")
cmd = "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psScript & """"
shell.Run cmd, 0, False
