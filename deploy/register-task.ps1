# ==========================================================
# 注册 Windows 计划任务：每天上午 9:00 触发一次。
#
# 触发 ≠ 执行：命令里带了 --respect-interval，程序内部会检查距上次运行
# 是否已满 config.yaml 里的 interval_days（默认 3 天），未满则直接跳过。
# 这样即使某天关机错过了，开机后也能自动补上，而多次触发不会重复推送。
#
# 用法（普通权限即可，不需要管理员）：
#   powershell -ExecutionPolicy Bypass -File deploy\register-task.ps1
#
# 默认会自动找同目录下的 exe；没有 exe 就回退到源码版 run-scout.bat。
# ==========================================================

param(
    [string]$RunTime = "09:00",
    [string]$TaskName = "SydneyDateScout",
    # 强制用源码版，即使 dist 里有 exe。
    # ⚠ exe 版的可写目录是 exe 自己所在的 dist\（见 scout/paths.py::data_dir），
    #   和源码版的 <项目根>\data\ 不是同一个地方 —— 两边的 config.yaml 与
    #   scout.db 各自独立。本机已装 Python，用源码版可以保证配置和历史只有一份，
    #   而且改了代码不用重新 pyinstaller。
    [switch]$UseSource
)

$ProjectDir = Split-Path -Parent $PSScriptRoot
$ExePath    = Join-Path $ProjectDir "dist\悉尼约会助理.exe"
$BatPath    = Join-Path $PSScriptRoot "run-scout.bat"

# 源码版直接调 pythonw.exe，**不经过 run-scout.bat**。
#
# 为什么绕开 .bat：本项目路径含中文（…\约会助手\）。cmd.exe 读取 .bat 用的是
# 系统 ANSI 代码页（简中机器上是 GBK），而 .bat 文件本身存的是 UTF-8，
# 于是路径和中文注释全部变成乱码 —— 实测 REM 注释行会被当成命令执行、
# `cd /d` 失效，任务退出码 1 且连日志文件都创建不出来，属于纯静默失败。
# 计划任务的动作是以 Unicode 存在任务 XML 里的，直接指向 pythonw.exe 没有这个问题。
#
# 日志由程序自己写到 data\out\scout.log（见 run.py::_setup_log），不依赖 shell 重定向。
$PythonW = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"

if ((Test-Path $ExePath) -and (-not $UseSource)) {
    $Execute   = $ExePath
    $Arguments = "--run --respect-interval"
    $WorkDir   = Split-Path -Parent $ExePath
    Write-Host "使用 exe：$ExePath"
} elseif (Test-Path $PythonW) {
    $Execute   = $PythonW
    $Arguments = "run.py run --respect-interval"
    $WorkDir   = $ProjectDir
    Write-Host "使用源码版：$PythonW run.py"
} elseif (Test-Path $ExePath) {
    $Execute   = $ExePath
    $Arguments = "--run --respect-interval"
    $WorkDir   = Split-Path -Parent $ExePath
    Write-Host "找不到 pythonw.exe，回退到 exe：$ExePath"
} else {
    Write-Error "既找不到 pythonw.exe（$PythonW），也找不到 $ExePath"
    exit 1
}

# 已存在同名任务则先删除，便于重复执行本脚本更新配置
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "已存在同名任务，先注销旧的..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

if ($Arguments) {
    $action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $WorkDir
} else {
    $action = New-ScheduledTaskAction -Execute $Execute -WorkingDirectory $WorkDir
}

$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

# StartWhenAvailable：错过触发时间（比如当时关机）则开机后尽快补跑
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                                         -DontStopIfGoingOnBatteries `
                                         -AllowStartIfOnBatteries

Register-ScheduledTask -TaskName $TaskName `
                       -Action   $action `
                       -Trigger  $trigger `
                       -Settings $settings `
                       -Description "悉尼约会提案助理：每天检查一次，满 interval_days 天则采集并弹窗提示" | Out-Null

Write-Host ""
Write-Host "已注册计划任务 '$TaskName'，每天 $RunTime 触发。"
Write-Host ""
Write-Host "常用操作："
Write-Host "  立即测试跑一次：  Start-ScheduledTask -TaskName $TaskName"
Write-Host "  查看任务状态：    Get-ScheduledTaskInfo -TaskName $TaskName"
Write-Host "  查看运行日志：    Get-Content '$WorkDir\data\out\scout.log' -Tail 30"
Write-Host "  取消定时：        Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
