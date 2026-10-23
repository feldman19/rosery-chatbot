# Live, readable view of Codex runs: follows each new run automatically.
# Usage: powershell -ExecutionPolicy Bypass -File tools\watch_codex.ps1
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Join-Path $env:USERPROFILE '.codex\sessions'
$events = Join-Path (Split-Path $PSScriptRoot -Parent) '.agents\events.jsonl'
$current = $null; $seen = 0; $started = $null; $lastBeat = Get-Date

function Say($text, $color = 'Gray') { Write-Host ("{0}  {1}" -f (Get-Date).ToString('HH:mm:ss'), $text) -ForegroundColor $color }

function Task-Name {
    $line = Get-Content $events -Encoding utf8 -Tail 50 | Where-Object { $_ -match 'codex_task_started' } | Select-Object -Last 1
    if ($line) { ($line | ConvertFrom-Json).task } else { 'משימה' }
}

function Describe($p) {
    $raw = if ($p.input) { [string]$p.input } elseif ($p.arguments) { [string]$p.arguments } else { '' }
    $cmd = if ($raw -match '"?cmd"?\s*:\s*"((?:[^"\\]|\\.)*)"') { $Matches[1] -replace '\\n', ' ' -replace '\\"', '"' } else { $raw }
    if ($p.name -match 'apply_patch' -or $raw -match '\*\*\* (Update|Add) File: ') {
        $files = [regex]::Matches($raw, '\*\*\* (?:Update|Add) File: ([^\\\n]+)') | ForEach-Object { Split-Path $_.Groups[1].Value -Leaf }
        return 'עורך: ' + (($files | Select-Object -Unique) -join ', ')
    }
    if ($cmd -match 'unittest|pytest') { return 'מריץ בדיקות' }
    if ($cmd -match '\bgit (diff|show|log|status)') { return 'בודק שינויים ב-git' }
    if ($cmd -match 'Get-Content|Select-String|\brg\b|\bcat\b|Get-ChildItem|\bls\b') {
        $file = [regex]::Match($cmd, '[\w\-]+\.(py|html|js|md|json)').Value
        return 'קורא קבצים' + ($(if ($file) { ": $file" } else { '' }))
    }
    if ($p.name -match 'wait|write_stdin') { return $null }
    $short = ($cmd -replace '\s+', ' ').Trim()
    if ($short.Length -gt 90) { $short = $short.Substring(0, 90) + '...' }
    return "מריץ: $short"
}

Write-Host 'צופה בריצות של Codex. Ctrl+C סוגר רק את החלון הזה, לא את Codex.' -ForegroundColor DarkGray
while ($true) {
    $file = Get-ChildItem $root -Recurse -Filter 'rollout-*.jsonl' | Sort-Object LastWriteTime | Select-Object -Last 1
    if ($file -and $file.FullName -ne $current) {
        $current = $file.FullName; $seen = 0; $started = Get-Date
        Write-Host ''
        Say ("ריצה חדשה של Codex: " + (Task-Name)) 'Cyan'
    }
    if ($current) {
        $lines = Get-Content $current -Encoding utf8
        if ($lines.Count -gt $seen) {
            foreach ($line in $lines[$seen..($lines.Count - 1)]) {
                try { $o = $line | ConvertFrom-Json } catch { continue }
                $p = $o.payload
                switch ($p.type) {
                    'agent_message' { Say ('Codex כתב: ' + (($p.message -replace '\s+', ' ').Trim())) 'Green' }
                    { $_ -in 'custom_tool_call', 'function_call' } { $d = Describe $p; if ($d) { Say $d 'Yellow' } }
                    'task_complete' { Say ('Codex סיים את הריצה (' + [int]((Get-Date) - $started).TotalMinutes + ' דקות).') 'Cyan' }
                }
            }
            $seen = $lines.Count; $lastBeat = Get-Date
        } elseif (((Get-Date) - $lastBeat).TotalSeconds -ge 60) {
            $alive = Get-Process codex -ErrorAction SilentlyContinue | Where-Object { $_.StartTime -ge $started.AddSeconds(-5) }
            Say ($(if ($alive) { 'Codex עדיין עובד...' } else { 'אין ריצה פעילה של Codex כרגע.' })) 'DarkGray'
            $lastBeat = Get-Date
        }
    }
    Start-Sleep -Seconds 2
}
