# Live, readable view of the latest Codex run: its messages and the commands it runs.
# Usage: powershell -ExecutionPolicy Bypass -File tools\watch_codex.ps1
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$root = Join-Path $env:USERPROFILE '.codex\sessions'
$file = Get-ChildItem $root -Recurse -Filter 'rollout-*.jsonl' | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $file) { Write-Host 'No Codex run found.'; exit 1 }
Write-Host "Codex run: $($file.Name)" -ForegroundColor Cyan
Write-Host '(Ctrl+C to stop watching; this does not stop Codex.)' -ForegroundColor DarkGray

function Short($text, $max = 240) {
    $one = ($text -replace '\s+', ' ').Trim()
    if ($one.Length -gt $max) { $one.Substring(0, $max) + '...' } else { $one }
}

Get-Content $file.FullName -Encoding utf8 -Wait -Tail 40 | ForEach-Object {
    try { $o = $_ | ConvertFrom-Json } catch { return }
    $p = $o.payload
    $time = ([datetime]$o.timestamp).ToLocalTime().ToString('HH:mm:ss')
    switch ($p.type) {
        'agent_message' { Write-Host "$time  Codex: $(Short $p.message 600)" -ForegroundColor Green }
        'custom_tool_call' { Write-Host "$time  > $($p.name): $(Short $p.input)" -ForegroundColor Yellow }
        'function_call' { Write-Host "$time  > $($p.name): $(Short $p.arguments)" -ForegroundColor Yellow }
        'task_complete' { Write-Host "$time  Codex finished." -ForegroundColor Cyan }
    }
}
