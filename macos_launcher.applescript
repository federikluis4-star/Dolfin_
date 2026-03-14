set appBundlePath to POSIX path of (path to me)
set distDirPath to do shell script "dirname " & quoted form of appBundlePath
set repoRootPath to do shell script "cd " & quoted form of (distDirPath & "/..") & " && pwd"
set launcherPath to repoRootPath & "/start_ui_server.sh"

try
	do shell script quoted form of launcherPath
	delay 1
	open location "http://127.0.0.1:8765"
on error errText number errNum
	display dialog "Support Copilot launcher error:" & return & errText & " (" & errNum & ")" buttons {"OK"} default button "OK" with icon stop
end try
