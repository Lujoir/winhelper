# Lenovo BIOS auto-power-on read-only probe (EyeTerm power-control P0 spike)
# Purpose: detect standard BIOS WMI interface and read RTC wake config
# Safety: READ-ONLY (Get-CimInstance / Get-CimClass / powercfg / schtasks queries)
# Run: admin PowerShell, or right-click lenovo_bios_probe.bat -> Run as administrator
# Output: Desktop\Lenovo_BIOS_Probe_<timestamp>.txt (ASCII safe)

$ErrorActionPreference = 'SilentlyContinue'
$stamp  = Get-Date -Format "yyyyMMdd_HHmmss"
$outFile = Join-Path ([Environment]::GetFolderPath('Desktop')) "Lenovo_BIOS_Probe_$stamp.txt"
$L = New-Object System.Collections.Generic.List[string]

$L.Add("== EyeTerm Lenovo BIOS read-only probe ==")
$L.Add("Time: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
$L.Add("IsAdmin: " + ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))

$L.Add("")
$L.Add("== 1. Machine ==")
$cs = Get-CimInstance Win32_ComputerSystem
$bios = Get-CimInstance Win32_BIOS
$L.Add("Manufacturer : " + $cs.Manufacturer)
$L.Add("Model        : " + $cs.Model)
$L.Add("SystemFamily : " + $cs.SystemFamily)
$L.Add("SystemSKU    : " + $cs.SystemSKUNumber)
$L.Add("BIOS         : " + $bios.SMBIOSBIOSVersion + "  (" + $bios.ReleaseDate + ")")

$L.Add("")
$L.Add("== 2. Lenovo* classes under root\wmi ==")
$cls = Get-CimClass -Namespace root/wmi | Where-Object { $_.CimClassName -like 'Lenovo*' } |
    Select-Object -ExpandProperty CimClassName | Sort-Object
if ($cls) { $cls | ForEach-Object { $L.Add($_) } } else { $L.Add("(none)") }

$hasSetting = $cls -contains 'Lenovo_BiosSetting'
$L.Add("")
$L.Add("== 3. Standard BIOS setting interface Lenovo_BiosSetting exists = " + $hasSetting + " ==")

if ($hasSetting) {
    $L.Add("")
    $L.Add("== 4. All BIOS settings (Item,Value) ==")
    $s = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosSetting
    $L.Add("TotalItems: " + @($s).Count)
    foreach ($i in $s) { $L.Add([string]$i.CurrentSetting) }

    $L.Add("")
    $L.Add("== 5. RTC / power-on / wake related items (filtered) ==")
    $hit = 0
    foreach ($i in $s) {
        if ($i.CurrentSetting -match '(?i)rtc|alarm|power.?on|wake|timer|automatic') {
            $L.Add("[HIT] " + $i.CurrentSetting); $hit++
        }
    }
    if ($hit -eq 0) { $L.Add("(no RTC/alarm related item matched)") }

    if ($cls -contains 'Lenovo_BiosPasswordSettings') {
        $L.Add("")
        $L.Add("== 6. BIOS password state (0=none) ==")
        $pw = Get-CimInstance -Namespace root/wmi -ClassName Lenovo_BiosPasswordSettings
        foreach ($p in $pw) { $L.Add([string]$p.PasswordState) }
    }
    if ($cls -contains 'Lenovo_GetBiosSelections') {
        $L.Add("")
        $L.Add("== 7. Selections interface Lenovo_GetBiosSelections exists (for P1 write) ==")
    }
    if ($cls -contains 'Lenovo_SetBiosSetting') {
        $L.Add("")
        $L.Add("== 8. Write interface Lenovo_SetBiosSetting exists (NOT used this run) ==")
    }
}

$L.Add("")
$L.Add("== 9. Power state (Fast Startup / sleep capability) ==")
$hb = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -Name HiberbootEnabled -ErrorAction SilentlyContinue
if ($null -ne $hb) {
    $L.Add("HiberbootEnabled = " + $hb.HiberbootEnabled + "  (1=on 0=off)")
} else { $L.Add("HiberbootEnabled key not present") }
$L.Add("--- powercfg /a ---")
$L.Add((powercfg /a 2>&1 | Out-String).Trim())

$L.Add("")
$L.Add("== 10. Active wake timers (powercfg /waketimers, needs admin) ==")
$wt = powercfg /waketimers 2>&1 | Out-String
$L.Add($wt.Trim())

$L.Add("")
$L.Add("== 11. Scheduled tasks with shutdown action (schtasks read-only) ==")
$st = schtasks /query /fo csv /v 2>&1 | Out-String
$matchCount = 0
foreach ($ln in ($st -split "`r?`n")) {
    if ($ln -match '(?i)shutdown') { $L.Add($ln.Trim()); $matchCount++ }
}
if ($matchCount -eq 0) { $L.Add("(no task with shutdown action found by EN keyword)") }

$L.Add("")
$L.Add("== Probe finished (read-only) ==")

$L | Out-File -FilePath $outFile -Encoding utf8
Write-Host ("Probe done. Result saved to: " + $outFile)
