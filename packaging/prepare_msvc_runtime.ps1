param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Destination | Out-Null
try {
    $roots = @("$env:SystemRoot\System32")
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $installations = & $vswhere -all -products '*' -property installationPath
        foreach ($installation in $installations) {
            $redist = Join-Path $installation 'VC\Redist\MSVC'
            if (Test-Path $redist) {
                foreach ($version in Get-ChildItem -LiteralPath $redist -Directory) {
                    $x64 = Join-Path $version.FullName 'x64'
                    if (Test-Path $x64) {
                        $roots += @(Get-ChildItem -LiteralPath $x64 -Directory -Filter 'Microsoft.VC*.CRT' | ForEach-Object { $_.FullName })
                    }
                }
            }
        }
    }
    $names = @('msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','msvcp140_atomic_wait.dll','msvcp140_codecvt_ids.dll','vcruntime140.dll','vcruntime140_1.dll')
    $records = @()
    foreach ($name in $names) {
        $candidates = @()
        $diagnostics = @()
        foreach ($root in $roots) {
            $source = Join-Path $root $name
            if (!(Test-Path $source)) { continue }
            $info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($source)
            $version = [version]::new($info.FileMajorPart,$info.FileMinorPart,$info.FileBuildPart,$info.FilePrivatePart)
            if ($version -lt [version]'14.43.0.0') { $diagnostics += "$version is too old"; continue }
            $signature = Get-AuthenticodeSignature -FilePath $source
            $subject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { '' }
            # Windows catalogue signatures may name Microsoft Windows rather than Microsoft Corporation.
            if ($signature.Status -ne 'Valid' -or $subject -notmatch '(?:^|,\s*)O=Microsoft Corporation(?:,|$)') {
                $diagnostics += "$version signature=$($signature.Status) signer=$subject"
                continue
            }
            $candidates += [pscustomobject]@{source=$source;version=$version}
        }
        if (!$candidates.Count) {
            if ($name -in @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')) {
                throw "No verified current Microsoft runtime for ${name}: $($diagnostics -join '; '). Install the current Microsoft Visual C++ x64 Redistributable."
            }
            continue
        }
        $chosen = $candidates | Sort-Object version -Descending | Select-Object -First 1
        Copy-Item -LiteralPath $chosen.source -Destination (Join-Path $Destination $name) -Force
        $records += @{file=$name;version=$chosen.version.ToString();sha256=(Get-FileHash $chosen.source -Algorithm SHA256).Hash.ToLower();signer='Microsoft Corporation';source='Verified Windows/Visual Studio Microsoft Visual C++ redistributable'}
    }
    $json = ConvertTo-Json -Depth 5 -InputObject @($records)
    [System.IO.File]::WriteAllText((Join-Path $Destination 'RUNTIME-SOURCES.json'),$json,[System.Text.UTF8Encoding]::new($false))
    Write-Host 'Verified current Microsoft x64 runtime files prepared.'
} catch {
    $message = $_.Exception.Message.Replace('%','%25').Replace("`r",'%0D').Replace("`n",'%0A')
    Write-Host "::error title=Microsoft runtime preparation::$message"
    throw
}
