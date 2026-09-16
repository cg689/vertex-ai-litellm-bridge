<#
  注册 LiteLLM 代理的保活计划任务

  用法（在 WorkBuddy 里执行）：
    把本文件内容贴进 PowerShell 工具，或
    powershell -NoProfile -ExecutionPolicy Bypass -File register-task.ps1

  设计要点（缺一不可，详见 SKILL.md 阶段 4）：
    1. LogonType=S4U  -> 非交互，不创建控制台窗口，没得被误关
    2. logon trigger 挂 Repetition 每 5 分钟 -> 看门狗，进程死了自动拉起
    3. MultipleInstances=IgnoreNew -> 重复触发不会起第二个实例
    4. ExecutionTimeLimit=PT0S -> 无限制，否则任务会被掐掉

  配套要求：start-litellm.bat 开头必须有端口守卫（幂等），
  否则看门狗每 5 分钟会起一个新实例。

  注意：WorkBuddy 的 PowerShell 工具不回传 stdout，本脚本把结果写到
  $env:TEMP\litellm-task-result.txt，用 Read 工具读。
#>

$TASK    = "ZCode LiteLLM Vertex Proxy"
$USERID  = "$env:USERDOMAIN\$env:USERNAME"
$BAT     = "C:\Users\$env:USERNAME\.zcode\litellm\start-litellm.bat"
$OUT     = "$env:TEMP\litellm-task-result.txt"
$lines   = @()

# ---- 1. 触发器：登录后 30 秒，之后每 5 分钟重复，持续 3650 天 ----
$logon = New-ScheduledTaskTrigger -AtLogOn
$logon.Delay = "PT30S"
$once = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
          -RepetitionInterval (New-TimeSpan -Minutes 5) `
          -RepetitionDuration (New-TimeSpan -Days 3650)
$logon.Repetition = $once.Repetition

# ---- 2. 动作 ----
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BAT`""

# ---- 3. 设置 ----
$set = New-ScheduledTaskSettingsSet `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit ([TimeSpan]::Zero) `
  -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# ---- 4. 注册（优先 S4U 无窗口，失败则退回交互式） ----
$done = $false
foreach ($lt in @("S4U", "Interactive")) {
  try {
    $prin = New-ScheduledTaskPrincipal -UserId $USERID -LogonType $lt -RunLevel Limited
    Register-ScheduledTask -TaskName $TASK -Action $action -Trigger $logon `
      -Settings $set -Principal $prin -Force -ErrorAction Stop | Out-Null
    $lines += "REGISTERED LogonType=$lt"
    $done = $true
    break
  } catch {
    $lines += "FAILED LogonType=$lt : $($_.Exception.Message)"
  }
}
if (-not $done) { $lines += "BOTH FAILED - task not registered" }

# ---- 5. 立即拉起一次 ----
Start-ScheduledTask -TaskName $TASK
Start-Sleep -Seconds 12

# ---- 6. 回读状态（每项都包 try，避免单点异常打断整个脚本）----
$t = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue
if ($t) {
  $i = Get-ScheduledTaskInfo -TaskName $TASK
  $lines += "State=$($t.State) Principal=$($t.Principal.LogonType)"
  $t.Triggers | ForEach-Object {
    $lines += "trigger=$($_.CimClass.CimClassName) delay=$($_.Delay) rep=$($_.Repetition.Interval)/$($_.Repetition.Duration)"
  }
  # Export-ScheduledTask 返回的 XML 偶尔为 $null，直接取属性会抛异常
  try {
    $xml = Export-ScheduledTask -TaskName $TASK -ErrorAction Stop
    if ($xml) {
      $lines += "MultipleInstances=$(([xml]$xml).Task.Settings.MultipleInstancesPolicy)"
    } else {
      $lines += "MultipleInstances=(Export-ScheduledTask 返回空)"
    }
  } catch {
    $lines += "MultipleInstances=(读取失败: $($_.Exception.Message))"
  }
  $lines += "ExecTimeLimit=$($t.Settings.ExecutionTimeLimit)"
  $lines += "LastRun=$($i.LastRunTime) Result=$($i.LastTaskResult)"
  $lines += "RESULT_HINT: 267009=运行中 / 2147946720=已在运行(正常) / 3221225786=窗口被关"
} else {
  $lines += "任务不存在，注册失败"
}

# netstat 输出在 PowerShell 里偶尔被截断，用 cmd 原样取
try {
  $ns = cmd /c 'netstat -ano | findstr "127.0.0.1:4000"'
  $lines += "port4000=" + (($ns -join " ;; ") -replace "\s+", " ")
} catch {
  $lines += "port4000=(netstat 读取失败)"
}
$lines += "port10808=" + ((cmd /c 'netstat -ano | findstr "127.0.0.1:10808"' -join " ;; ") -replace "\s+", " ")

[IO.File]::WriteAllLines($OUT, $lines)
