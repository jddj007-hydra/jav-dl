# Hands a javplayer: URL to PotPlayer.
# Roots match JAVPlayer. javplayer-roots.txt beside this script overrides a non-empty line.
param([string]$Url)

$script:JavPlayerHome = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

function Get-DefaultRoots {
    $western = 'Z:\6t\' + [char]0x6B27 + [char]0x7F8E
    return @{
        jav = 'Z:\media'
        western = $western
        vr = 'Z:\vrporn\western'
        jav_vr = ''
        potplayer = ''
    }
}

function Read-RootFile([string]$path) {
    $roots = Get-DefaultRoots
    if (-not (Test-Path -LiteralPath $path)) { return $roots }
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $text = [IO.File]::ReadAllText($path, $utf8).TrimStart([char]0xFEFF)
    foreach ($line in ($text -split '\r?\n')) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) { continue }
        $eq = $trimmed.IndexOf('=')
        if ($eq -lt 1) { continue }
        $name = $trimmed.Substring(0, $eq).Trim().ToLower()
        $value = $trimmed.Substring($eq + 1).Trim()
        if ($roots.ContainsKey($name) -and $value) { $roots[$name] = $value }
    }
    return $roots
}

function Get-JavVrRoot([string]$vrRoot) {
    $trimmed = $vrRoot.Trim().TrimEnd('\', '/')
    if (-not $trimmed) { return $null }
    $parts = @($trimmed.Replace('/', '\').Split('\') | Where-Object { $_ -ne '' })
    if ($parts.Count -lt 2) { return $null }
    $parent = ($parts[0..($parts.Count - 2)] -join '\')
    if ($trimmed.StartsWith('\\')) { $parent = '\\' + $parent }
    return ($parent + '\jav')
}

function Join-LibraryPath([string]$root, [string]$relative) {
    if ([string]::IsNullOrWhiteSpace($root) -or [string]::IsNullOrWhiteSpace($relative)) { return $null }
    $rel = $relative.Replace('/', '\').Trim()
    if ($rel -match '^[A-Za-z]:' -or $rel.StartsWith('\\') -or $rel.StartsWith('/')) { return $null }
    $parts = @($rel.Split('\') | Where-Object { $_ -ne '' })
    if ($parts.Count -eq 0) { return $null }
    foreach ($part in $parts) {
        if ($part -eq '.' -or $part -eq '..') { return $null }
        if ($part.IndexOfAny([char[]]@(':', '*', '?', '"', '<', '>', '|')) -ge 0) { return $null }
    }
    $rootFull = [IO.Path]::GetFullPath($root.Trim().TrimEnd('\', '/'))
    $joined = [IO.Path]::GetFullPath((Join-Path $rootFull ($parts -join '\')))
    $prefix = $rootFull.TrimEnd('\') + '\'
    if (-not $joined.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
    return $joined
}

function Convert-PlayQuery([string]$raw) {
    $text = if ($raw) { $raw } else { '' }
    if ($text -match '^(?i)javplayer:') { $text = $text.Substring($Matches[0].Length) }
    $text = $text.TrimStart('/')
    $qIndex = $text.IndexOf('?')
    if ($qIndex -ge 0) { $text = $text.Substring($qIndex + 1) }
    $map = @{}
    foreach ($pair in ($text -split '&')) {
        if (-not $pair) { continue }
        $bits = $pair -split '=', 2
        $name = [Uri]::UnescapeDataString(($bits[0] -replace '\+', ' '))
        $value = ''
        if ($bits.Length -gt 1) {
            $value = [Uri]::UnescapeDataString(($bits[1] -replace '\+', ' '))
        }
        $map[$name.ToLower()] = $value
    }
    return $map
}

function Resolve-PlayTarget($roots, [string]$kind, [string]$relative) {
    $key = if ($kind) { $kind.ToLower() } else { '' }
    $root = $null
    switch ($key) {
        'jav' { $root = $roots.jav }
        'western' { $root = $roots.western }
        'vr' { $root = $roots.vr }
        'jav_vr' {
            if ($roots.jav_vr) { $root = $roots.jav_vr }
            else { $root = Get-JavVrRoot $roots.vr }
        }
        default { return $null }
    }
    if ($key -eq 'vr') {
        if ($relative.StartsWith('vr/')) { $relative = $relative.Substring(3) }
        elseif ($relative.StartsWith('vr\')) { $relative = $relative.Substring(3) }
    }
    return Join-LibraryPath $root $relative
}

function Find-PotPlayer([string]$configured) {
    if ($configured -and (Test-Path -LiteralPath $configured)) { return $configured }
    foreach ($viewName in @('Registry64', 'Registry32')) {
        $view = [Microsoft.Win32.RegistryView]::$viewName
        foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
            try {
                $key = $base.OpenSubKey('Software\DAUM\PotPlayer64')
                if (-not $key) { continue }
                try {
                    $path = $key.GetValue('ProgramPath')
                    if ($path -and (Test-Path -LiteralPath $path)) { return [string]$path }
                    $folder = $key.GetValue('ProgramFolder')
                    if ($folder) {
                        $exe = Join-Path $folder 'PotPlayerMini64.exe'
                        if (Test-Path -LiteralPath $exe) { return $exe }
                    }
                } finally { $key.Close() }
            } finally { $base.Close() }
        }
    }
    foreach ($fallback in @(
        'C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe',
        'C:\Program Files\DAUM\PotPlayer\PotPlayerMini.exe'
    )) {
        if (Test-Path -LiteralPath $fallback) { return $fallback }
    }
    return $null
}

function Show-PlayError([string]$message) {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'jav-dl') | Out-Null
}

function Start-PotPlayer([string]$player, [string]$video) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $player
    $psi.Arguments = '"' + $video + '"'
    $psi.UseShellExecute = $false
    [void][Diagnostics.Process]::Start($psi)
}

function Invoke-JavPlayerPlay([string]$RawUrl) {
    $roots = Read-RootFile (Join-Path $script:JavPlayerHome 'javplayer-roots.txt')
    $query = Convert-PlayQuery $RawUrl
    $kind = [string]$query['kind']
    $relative = [string]$query['path']
    if (-not $kind -or -not $relative) {
        Show-PlayError '播放地址不完整。'
        return
    }
    $video = Resolve-PlayTarget $roots $kind $relative
    if (-not $video) {
        Show-PlayError '播放路径无效。'
        return
    }
    if (-not (Test-Path -LiteralPath $video)) {
        Show-PlayError ('视频文件不在了：' + $video)
        return
    }
    $player = Find-PotPlayer $roots.potplayer
    if (-not $player) {
        Show-PlayError '找不到 PotPlayer。在 javplayer-roots.txt 里写上 potplayer=完整路径。'
        return
    }
    Start-PotPlayer $player $video
}

if ($MyInvocation.InvocationName -ne '.') {
    if ($Url) { Invoke-JavPlayerPlay $Url }
}
