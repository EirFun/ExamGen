param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force $Destination | Out-Null
try {
$names = @('msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','msvcp140_atomic_wait.dll','msvcp140_codecvt_ids.dll','vcruntime140.dll','vcruntime140_1.dll','concrt140.dll')
$records = @()
foreach ($name in $names) {
    $source = Join-Path "$env:SystemRoot\System32" $name
    if (!(Test-Path $source)) {
        if ($name -in @('msvcp140.dll','vcruntime140.dll','vcruntime140_1.dll')) { throw "Install the current Microsoft Visual C++ x64 Redistributable: missing $name" }
        continue
    }
    $signature = Get-AuthenticodeSignature -FilePath $source
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.GetNameInfo('SimpleName',$false) -ne 'Microsoft Corporation') {
        throw "Microsoft runtime signature verification failed: $name"
    }
    $info = [System.Diagnostics.FileVersionInfo]::GetVersionInfo($source)
    $version = [version]::new($info.FileMajorPart,$info.FileMinorPart,$info.FileBuildPart,$info.FilePrivatePart)
    if ($version -lt [version]'14.43.0.0') { throw "Microsoft runtime $name ($version) is too old; install the current x64 Redistributable." }
    Copy-Item -LiteralPath $source -Destination (Join-Path $Destination $name) -Force
    $records += @{file=$name; version=$version.ToString(); sha256=(Get-FileHash $source -Algorithm SHA256).Hash.ToLower(); signer='Microsoft Corporation'; source='Windows System32 Microsoft Visual C++ runtime'}
}
$json = ConvertTo-Json -Depth 5 -InputObject @($records)
[System.IO.File]::WriteAllText((Join-Path $Destination 'RUNTIME-SOURCES.json'), $json, [System.Text.UTF8Encoding]::new($false))
Write-Host 'Verified current Microsoft x64 runtime files prepared.'

} catch {
    $message = $_.Exception.Message.Replace("%","%25").Replace("`r","%0D").Replace("`n","%0A")
    Write-Host "::error title=Microsoft runtime preparation::$message"
    throw
}
