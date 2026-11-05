param(
    [string]$LogPath = "C:\Temp\GPU_Disable_Log.txt"
)

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] $Message"
    $folder = Split-Path $LogPath -Parent
    if (-not (Test-Path $folder)) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
    }
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
}

function Main {
    Write-Log "بدء تنفيذ سكريبت تعطيل كروت الشاشة"

    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")
    if (-not $isAdmin) {
        Write-Log "خطأ: السكريبت يحتاج صلاحيات Administrator"
        return
    }

    try {
        # البحث عن كروت الشاشة عبر GUID المحفوظ في النظام (أكثر استقراراً من Class Name)
        $displayAdapters = Get-CimInstance -Namespace "root\cimv2" -Query "SELECT * FROM Win32_PnPSignedDriver WHERE ClassGuid = '{4d36e968-e325-11ce-bfc1-08002be10318}'"
        
        if ($displayAdapters -and $displayAdapters.Count -gt 0) {
            Write-Log "تم إيجاد $($displayAdapters.Count) كرت شاشة."
            
            foreach ($adapter in $displayAdapters) {
                try {
                    $instanceId = $adapter.PNPDeviceID
                    $pnpDevice = Get-PnpDevice -InstanceId $instanceId -ErrorAction SilentlyContinue
                    
                    if ($pnpDevice -and $pnpDevice.Status -eq 'OK') {
                        Disable-PnpDevice -InstanceId $pnpDevice.InstanceId -Confirm:$false -ErrorAction Stop | Out-Null
                        Write-Log "تم تعطيل: $($pnpDevice.FriendlyName)"
                    }
                } catch {
                    Write-Log "فشل التعطيل لـ $($adapter.PNPDeviceID): $($_.Exception.Message)"
                }
            }
        } else {
            Write-Log "ما لقيت أي كرت شاشة مهيأ."
        }

        Write-Log "انتهت العملية"
    }
    catch {
        Write-Log "خطأ عام: $($_.Exception.Message)"
    }
}

Main