# Place lab files for attacker collection and payload-upload tasks.
# Run on each member host as -Role Member, and on the attacker host as -Role Attacker.
# The planner does not receive these paths until collection.remote-file-search writes them
# into state.json hosts[].discovered_files.

param(
    [ValidateSet("Member", "Attacker")]
    [string]$Role = "Member",
    [int64]$DocumentBytes = 64MB,
    [string]$Root = "C:\HolyFW",
    [string]$ToolsDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-LabFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][int64]$Bytes
    )
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write)
    try {
        $header = [System.Text.Encoding]::ASCII.GetBytes("HOLYFW-LAB`n")
        $stream.Write($header, 0, $header.Length)
        if ($Bytes -gt $header.Length) {
            $stream.SetLength($Bytes)
        }
    } finally {
        $stream.Dispose()
    }
    Write-Output $Path
}

if ($Role -eq "Attacker") {
    if (-not $ToolsDir) {
        $ToolsDir = Join-Path $PSScriptRoot "skills\ad-attack\tools"
    }
    New-LabFile -Path (Join-Path $ToolsDir "lab-payload-1m.bin") -Bytes 1MB
    New-LabFile -Path (Join-Path $ToolsDir "lab-payload-20m.bin") -Bytes 20MB
    New-LabFile -Path (Join-Path $ToolsDir "lab-payload-64m.bin") -Bytes 64MB
    return
}

@(
    "Documents\Finance\annual-report.bin",
    "Desktop\Project\project-notes.bin",
    "Shares\Engineering\package.bin",
    "Backups\host-backup.bin",
    "Exports\directory-export.bin"
) | ForEach-Object {
    New-LabFile -Path (Join-Path $Root $_) -Bytes $DocumentBytes
}
