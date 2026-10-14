# Registers the javplayer: protocol for the current Windows user.
# Copies the player script to %LOCALAPPDATA%\jav-dl and leaves an existing roots file in place.
$ErrorActionPreference = 'Stop'

$dest = Join-Path $env:LOCALAPPDATA 'jav-dl'
New-Item -ItemType Directory -Force -Path $dest | Out-Null

$source = Join-Path $PSScriptRoot 'javplayer-play.ps1'
$play = Join-Path $dest 'javplayer-play.ps1'
Copy-Item -LiteralPath $source -Destination $play -Force

$rootsPath = Join-Path $dest 'javplayer-roots.txt'
if (-not (Test-Path -LiteralPath $rootsPath)) {
    $western = 'Z:\6t\' + [char]0x6B27 + [char]0x7F8E
    $lines = @(
        'jav=Z:\media',
        "western=$western",
        'vr=Z:\vrporn\western',
        'jav_vr=',
        'potplayer='
    )
    $utf8 = New-Object System.Text.UTF8Encoding $true
    [IO.File]::WriteAllLines($rootsPath, $lines, $utf8)
}

$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$command = "`"$ps`" -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$play`" `"%1`""
$class = 'HKCU:\Software\Classes\javplayer'
New-Item -Path $class -Force | Out-Null
Set-Item -Path $class -Value 'URL:javplayer Protocol'
New-ItemProperty -Path $class -Name 'URL Protocol' -Value '' -PropertyType String -Force | Out-Null
$cmdKey = Join-Path $class 'shell\open\command'
New-Item -Path $cmdKey -Force | Out-Null
Set-Item -Path $cmdKey -Value $command

Write-Output "registered $command"
Write-Output "roots $rootsPath"
