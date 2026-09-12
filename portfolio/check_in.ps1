# check_in.ps1 — Scheduled portfolio check-in.
#
# Runs the gap check and pushes a Telegram nudge ONLY when something warrants
# one. Silence is the expected outcome most of the time and is correct: a nudge
# that arrives on a schedule regardless of whether anything happened is one you
# learn to dismiss, and then the one that mattered gets dismissed too.
#
# Register it to run daily (it decides for itself whether to speak):
#
#   $action  = New-ScheduledTaskAction -Execute "powershell.exe" `
#                -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\dev\healthcare_analytics\portfolio\check_in.ps1"
#   $trigger = New-ScheduledTaskTrigger -Daily -At 8am
#   Register-ScheduledTask -TaskName "Portfolio check-in" -Action $action -Trigger $trigger
#
# Remove with:  Unregister-ScheduledTask -TaskName "Portfolio check-in"

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo

# Refresh prices first so any check reasons over current values, then decide
# whether there is anything worth saying.
python portfolio\enrich.py --prices 2>&1 | Out-Null
python portfolio\gaps.py --notify
