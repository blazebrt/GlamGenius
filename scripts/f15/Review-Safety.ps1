# Functions only: the executable operator remains a separate bounded process.
function Stop-F15OwnedProcess([Diagnostics.Process]$Process) {
    if ($Process.HasExited) { return }
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        $Process.Kill($true)
    } else {
        # Exact live owned PID; /T includes its descendants without enumerating
        # or displaying unrelated command lines. Never pass a credential.
        $kill=[Diagnostics.ProcessStartInfo]::new()
        $kill.FileName=Join-Path $env:SystemRoot 'System32/taskkill.exe'
        $kill.Arguments='/PID '+$Process.Id+' /T /F'
        $kill.UseShellExecute=$false; $kill.CreateNoWindow=$true
        $kill.RedirectStandardOutput=$true; $kill.RedirectStandardError=$true
        $child=[Diagnostics.Process]::Start($kill)
        try { if (-not $child.WaitForExit(30000) -or $child.ExitCode -ne 0) { throw 'OWNED_PROCESS_CLEANUP_FAILED' } }
        finally { $child.Dispose() }
    }
    if (-not $Process.WaitForExit(30000)) { throw 'OWNED_PROCESS_CLEANUP_FAILED' }
}

function Invoke-F15IndependentCleanup([System.Collections.IDictionary]$Actions) {
    $f15Failures = [Collections.Generic.List[string]]::new()
    foreach ($f15Label in @('dump_clients','local_container','local_volume','sql_files','workspace','credential_process')) {
        try { & $Actions[$f15Label] } catch { $f15Failures.Add($f15Label) }
    }
    return [pscustomobject]@{ Success=($f15Failures.Count -eq 0); Failures=@($f15Failures) }
}

function Assert-F15DumpImage {
    param([string]$Docker, [string]$Tag, [string]$Dockerfile, [string]$Certificate)
    $f15ExpectedId = 'sha256:afeb281c749d2637851439e6266abd1a1e1cbab7c3245d093783355776a89d8d'
    $f15Base = 'public.ecr.aws/supabase/postgres@sha256:06ddc7962e11ab0f4f0334fd05671e97c30ea202f6e6a7113800bd3d6e416108'
    $f15CaHash = '700723581420dd1ac98fd7e9ac529f0ef210eadcaf87fc868a3ad7d114c2f3b7'
    if ((Get-FileHash -LiteralPath $Dockerfile -Algorithm SHA256).Hash.ToLowerInvariant() -ne '6591b0aa555a89ac0c2a20a5aeefcfccffde66b34177d9f9964b2db98dd8cef6' -or
        (Get-FileHash -LiteralPath $Certificate -Algorithm SHA256).Hash.ToLowerInvariant() -ne $f15CaHash) { throw 'DUMP_SOURCE_IDENTITY_MISMATCH' }
    $f15ImageResult = Invoke-F15Native $Docker @('image','inspect',$Tag,'--format','{{json .}}')
    Assert-F15NativeSuccess $f15ImageResult 'dump_image_identity'
    $f15ImageInfo = $f15ImageResult.Output | ConvertFrom-Json
    if ($f15ImageInfo.Id -cne $f15ExpectedId) { throw 'DUMP_IMAGE_IDENTITY_MISMATCH' }
    $f15BaseResult = Invoke-F15Native $Docker @('image','inspect',$f15Base,'--format','{{json .}}')
    Assert-F15NativeSuccess $f15BaseResult 'dump_base_identity'
    $f15BaseInfo = $f15BaseResult.Output | ConvertFrom-Json
    if ($f15BaseInfo.RepoDigests -notcontains $f15Base -or @($f15ImageInfo.RootFS.Layers).Count -le @($f15BaseInfo.RootFS.Layers).Count) { throw 'DUMP_BASE_IDENTITY_MISMATCH' }
    for ($f15Layer=0; $f15Layer -lt @($f15BaseInfo.RootFS.Layers).Count; $f15Layer++) {
        if ($f15ImageInfo.RootFS.Layers[$f15Layer] -cne $f15BaseInfo.RootFS.Layers[$f15Layer]) { throw 'DUMP_BASE_IDENTITY_MISMATCH' }
    }
    foreach ($f15RequiredEnv in @('PGSSLMODE=verify-full','PGSSLROOTCERT=/etc/f15/supabase-prod-ca-2021.crt','PGCONNECT_TIMEOUT=10','PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=120000')) {
        if ($f15ImageInfo.Config.Env -notcontains $f15RequiredEnv) { throw 'DUMP_TLS_IDENTITY_MISMATCH' }
    }
    $f15CaResult = Invoke-F15Native $Docker @('run','--rm','--network','none','--entrypoint','sha256sum',$f15ExpectedId,'/etc/f15/supabase-prod-ca-2021.crt')
    Assert-F15NativeSuccess $f15CaResult 'dump_embedded_ca'
    if (($f15CaResult.Output -split '\s+')[0] -cne $f15CaHash) { throw 'DUMP_EMBEDDED_CA_MISMATCH' }
    return $f15ExpectedId
}

function Start-F15PinnedDockerProxy([string]$Python, [string]$DockerPipe, [string]$Tag, [string]$Image) {
    $f15ProxyStart = [Diagnostics.ProcessStartInfo]::new()
    $f15ProxyStart.FileName=$Python
    $f15ProxyStart.Arguments=(@((Join-Path $PSScriptRoot 'pinned-docker-proxy.py'),'--pipe',$DockerPipe,'--tag',$Tag,'--image',$Image) | ForEach-Object { ConvertTo-F15NativeArgument $_ }) -join ' '
    $f15ProxyStart.UseShellExecute=$false; $f15ProxyStart.CreateNoWindow=$true
    $f15ProxyStart.RedirectStandardInput=$true; $f15ProxyStart.RedirectStandardOutput=$true; $f15ProxyStart.RedirectStandardError=$true
    # The bridge receives this run's password through its protected stdin only.
    foreach ($f15SecretVariable in @('PGPASSWORD','PGPASSFILE')) { $f15ProxyStart.EnvironmentVariables.Remove($f15SecretVariable) }
    $f15Proxy = [Diagnostics.Process]::new(); $f15Proxy.StartInfo=$f15ProxyStart
    [void]$f15Proxy.Start()
    $f15ReadyTask=$f15Proxy.StandardOutput.ReadLineAsync()
    if (-not $f15ReadyTask.Wait(15000)) { $f15Proxy.Kill(); throw 'PINNED_PROXY_START_FAILED' }
    $f15Ready = $f15ReadyTask.Result | ConvertFrom-Json
    if ($f15Ready.port -lt 1024 -or $f15Ready.port -gt 65535) { $f15Proxy.Kill(); throw 'PINNED_PROXY_START_FAILED' }
    return [pscustomobject]@{Process=$f15Proxy; Host=('tcp://127.0.0.1:'+$f15Ready.port); Token=$f15Ready.token}
}
