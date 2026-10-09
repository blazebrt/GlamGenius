param([Parameter(Mandatory=$true)][string]$CliPath,
      [Parameter(Mandatory=$true)][string]$PythonPath,
      [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
      [Parameter(Mandatory=$true)][string]$CertificatePath,
      [switch]$InjectArgvMutant)
$ErrorActionPreference='Stop'
$f15Root=[IO.Path]::GetFullPath($EvidenceDirectory)
[void][IO.Directory]::CreateDirectory($f15Root)
$f15Evidence=@{}
$f15Operator=Join-Path $PSScriptRoot 'Invoke-StoreB-Drill.ps1'
$f15Tokens=$null; $f15ParseErrors=$null
$f15Ast=[Management.Automation.Language.Parser]::ParseFile($f15Operator,[ref]$f15Tokens,[ref]$f15ParseErrors)
if ($f15ParseErrors.Count) { throw 'OPERATOR_PARSE_FAILED' }
foreach ($f15Function in @('ConvertTo-F15NativeArgument','Invoke-F15Native','Assert-F15NativeSuccess')) {
    $f15Def=$f15Ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $f15Function},$true)
    Invoke-Expression $f15Def.Extent.Text
}
. (Join-Path $PSScriptRoot 'Review-Safety.ps1')
$f15Docker=(Get-Command docker.exe).Source
$f15Results=[Collections.Generic.List[object]]::new()
$f15Tag='public.ecr.aws/supabase/postgres:17.11.0.004-f15-tls-20261008'
$f15Image=Assert-F15DumpImage $f15Docker $f15Tag (Join-Path $PSScriptRoot 'Dockerfile.dump-tls') $CertificatePath
$f15Results.Add(@{test='actual_immutable_image_base_source_ca_tls';passed=$true})
foreach ($f15Failure in @('dump_clients','local_container','local_volume')) {
    $f15Probe=Join-Path $f15Root ([Guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($f15Probe)
    foreach ($name in @('roles.sql','schema.sql','data.sql')) { [IO.File]::WriteAllText((Join-Path $f15Probe $name),'-- synthetic only') }
    $f15Actions=[ordered]@{}
    foreach ($label in @('dump_clients','local_container','local_volume')) { $f15Actions[$label]={}.GetNewClosure() }
    $f15Actions[$f15Failure]={throw 'SYNTHETIC_DOCKER_FAILURE'}
    $f15Actions.sql_files={foreach ($name in @('roles.sql','schema.sql','data.sql')) { [IO.File]::Delete((Join-Path $f15Probe $name)) }}
    $f15Actions.workspace={[IO.Directory]::Delete($f15Probe,$false)}
    $f15Cleaned=[Collections.Generic.List[string]]::new()
    $f15Actions.credential_process={$f15Cleaned.Add('credential_process')}
    $f15Cleanup=Invoke-F15IndependentCleanup $f15Actions
    if ($f15Cleanup.Success -or $f15Cleanup.Failures.Count -ne 1 -or $f15Cleanup.Failures[0] -ne $f15Failure -or
        [IO.Directory]::Exists($f15Probe) -or $f15Cleaned.Count -ne 1) { throw 'CLEANUP_INDEPENDENCE_REGRESSION' }
    $f15Results.Add(@{test=('cleanup_'+$f15Failure+'_failure');passed=$true;private_fixture_deleted=$true})
}
# Execute identity/CA mutants using the real immutable-image inspection except
# the one adversarial returned field. No tag or image is actually replaced.
$f15RealNative=(Get-Command Invoke-F15Native).ScriptBlock
foreach ($f15Mutation in @('tag','ca')) {
    function Invoke-F15Native {
        param([string]$Executable,[string[]]$Arguments,[hashtable]$ChildEnvironment=@{},[string]$InputText=$null,[int]$TimeoutSeconds=600)
        $r=& $f15RealNative $Executable $Arguments $ChildEnvironment $InputText $TimeoutSeconds
        if ($f15Mutation -eq 'tag' -and $Arguments -contains 'inspect' -and $Arguments -contains $f15Tag) {
            $j=$r.Output | ConvertFrom-Json; $j.Id='sha256:'+('0'*64); $r.Output=$j | ConvertTo-Json -Depth 100 -Compress
        }
        if ($f15Mutation -eq 'ca' -and $Arguments -contains 'sha256sum') { $r.Output=('0'*64)+'  /etc/f15/supabase-prod-ca-2021.crt' }
        return $r
    }
    $rejected=$false
    try { [void](Assert-F15DumpImage $f15Docker $f15Tag (Join-Path $PSScriptRoot 'Dockerfile.dump-tls') $CertificatePath) }
    catch { $rejected=$_.Exception.Message -in @('DUMP_IMAGE_IDENTITY_MISMATCH','DUMP_EMBEDDED_CA_MISMATCH') }
    if (-not $rejected) { throw 'IMAGE_MUTANT_SURVIVED' }
    $f15Results.Add(@{test=('image_'+$f15Mutation+'_mutant');passed=$true;mutant_killed=$true})
}
Set-Item Function:Invoke-F15Native $f15RealNative
$f15Proxy=$null; $f15CliChild=$null; $f15ShellChild=$null
$f15Sentinel=[Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N')
$f15OwnedIds=[Collections.Generic.List[int]]::new()
$f15Observed=[Collections.Generic.HashSet[int]]::new()
$f15Work=Join-Path $f15Root 'cli-work'
$f15Listener=[Net.Sockets.TcpListener]::new([Net.IPAddress]::Any,0)
$f15Listener.Start()
$f15SyntheticPort=$f15Listener.LocalEndpoint.Port
[void][IO.Directory]::CreateDirectory((Join-Path $f15Work 'supabase/.temp'))
[IO.File]::WriteAllText((Join-Path $f15Work 'supabase/.temp/postgres-version'),'17.11.0.004-f15-tls-20261008')
try {
    $f15Proxy=Start-F15PinnedDockerProxy $PythonPath '\\.\pipe\dockerDesktopLinuxEngine' $f15Tag $f15Image
    $f15ProxyErrors=$f15Proxy.Process.StandardError.ReadToEndAsync()
    $f15Proxy.Process.StandardInput.WriteLine((@{password=$f15Sentinel} | ConvertTo-Json -Compress)); $f15Proxy.Process.StandardInput.Flush()
    if ($f15Proxy.Process.StandardOutput.ReadLine() -ne 'CREDENTIAL_CHANNEL_READY') { throw 'PROXY_CREDENTIAL_NOT_READY' }
    $f15OwnedIds.Add($f15Proxy.Process.Id)
    $f15ShellStart=[Diagnostics.ProcessStartInfo]::new()
    $f15ShellStart.FileName=(Get-Command pwsh.exe).Source
    $f15ShellStart.Arguments='-NoProfile -NonInteractive -Command "Start-Sleep -Seconds 15"'
    $f15ShellStart.UseShellExecute=$false; $f15ShellStart.CreateNoWindow=$true
    $f15ShellStart.Environment['PGPASSWORD']=$f15Sentinel
    $f15ShellChild=[Diagnostics.Process]::Start($f15ShellStart)
    $f15ShellStart.Environment.Remove('PGPASSWORD') | Out-Null
    $f15OwnedIds.Add($f15ShellChild.Id)
    $f15Start=[Diagnostics.ProcessStartInfo]::new()
    $f15Start.FileName=[IO.Path]::GetFullPath($CliPath)
    $f15Args=@('db','dump','--db-url',('postgresql://postgres@host.docker.internal:'+$f15SyntheticPort+'/postgres?sslmode=verify-full'),'--workdir',$f15Work,'--file',(Join-Path $f15Root 'synthetic-dump.sql'),'--log-level','none')
    $f15Start.Arguments=($f15Args | ForEach-Object { ConvertTo-F15NativeArgument $_ }) -join ' '
    # Deliberately unsafe SYNTHETIC sentinel only. The real operator has no
    # such switch. The observer must reject this actual child-process mutant.
    if ($InjectArgvMutant) { $f15Start.Arguments += ' --password '+$f15Sentinel }
    $f15Start.UseShellExecute=$false; $f15Start.CreateNoWindow=$true; $f15Start.RedirectStandardOutput=$true; $f15Start.RedirectStandardError=$true
    $f15Start.Environment['PGPASSWORD']=$f15Sentinel; $f15Start.Environment['DOCKER_HOST']=$f15Proxy.Host; $f15Start.Environment['DOCKER_CUSTOM_HEADERS']='X-F15-Run='+$f15Proxy.Token; $f15Start.Environment['DOCKER_CONTEXT']=''; $f15Start.Environment['SUPABASE_USE_SLIM_IMAGES']='false'
    $f15CliChild=[Diagnostics.Process]::Start($f15Start)
    $f15Start.Environment.Remove('PGPASSWORD') | Out-Null; $f15Start.Arguments=''
    $f15OwnedIds.Add($f15CliChild.Id)
    $f15Out=$f15CliChild.StandardOutput.ReadToEndAsync(); $f15Err=$f15CliChild.StandardError.ReadToEndAsync()
    $f15Deadline=[DateTime]::UtcNow.AddSeconds(35)
    $f15DockerCommandObserved=$false
    $f15DockerChildObserved=$false
    while ([DateTime]::UtcNow -lt $f15Deadline) {
        foreach ($identity in $f15OwnedIds) {
            $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$identity) -ErrorAction Stop
            if ($p) { if ($p.CommandLine.Contains($f15Sentinel)) { throw 'SENTINEL_IN_OWNED_ARGV' }; [void]$f15Observed.Add($identity) }
        }
        # Query children of task-owned PIDs only; never enumerate unrelated argv.
        foreach ($parent in @($f15OwnedIds)) {
            foreach ($child in @(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$parent))) {
                if ($child.CommandLine -and $child.CommandLine.Contains($f15Sentinel)) { throw 'SENTINEL_IN_CHILD_ARGV' }
                if ($child.Name -eq 'docker.exe') { $f15DockerChildObserved=$true }
            }
        }
        $ids=Invoke-F15Native $f15Docker @('ps','-aq','--filter',('label=glamgenius.f15.run='+$f15Proxy.Token))
        Assert-F15NativeSuccess $ids 'owned_sentinel_clients'
        foreach ($id in ($ids.Output -split "`r?`n" | Where-Object { $_ })) {
            $inspect=Invoke-F15Native $f15Docker @('inspect','--format','{{.Image}}|{{json .Config.Cmd}}',$id)
            Assert-F15NativeSuccess $inspect 'owned_sentinel_command'
            if ($inspect.Output.Contains($f15Sentinel) -or -not $inspect.Output.StartsWith($f15Image+'|')) { throw 'SENTINEL_OR_MUTABLE_IMAGE_IN_DOCKER_COMMAND' }
            $f15DockerCommandObserved=$true
        }
        if ($f15CliChild.HasExited) { break }
        Start-Sleep -Milliseconds 50
    }
    if (-not $f15CliChild.HasExited) { throw 'LOCAL_SENTINEL_TIMEOUT' }
    if ($f15Out.Result.Contains($f15Sentinel) -or $f15Err.Result.Contains($f15Sentinel)) { throw 'SENTINEL_IN_NATIVE_OUTPUT' }
    if (-not $f15Observed.Contains($f15ShellChild.Id) -or -not $f15Observed.Contains($f15CliChild.Id) -or -not $f15DockerCommandObserved -or -not $f15DockerChildObserved) {
        Write-Output (@{shell_observed=$f15Observed.Contains($f15ShellChild.Id);cli_observed=$f15Observed.Contains($f15CliChild.Id);docker_observed=$f15DockerCommandObserved;cli_exit=$f15CliChild.ExitCode;cli_error=$f15Err.Result.Replace($f15Sentinel,'[REDACTED]')} | ConvertTo-Json -Compress)
        throw 'OWNED_PROCESS_PROOF_INCOMPLETE'
    }
    $repo=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
    $tracked=& git -c ('safe.directory='+$repo.Replace('\','/')) -C $repo ls-files --cached --others --exclude-standard
    foreach ($file in $tracked) {
        if ([IO.File]::ReadAllText((Join-Path $repo $file)).Contains($f15Sentinel)) { throw 'SENTINEL_IN_REPOSITORY' }
    }
    $f15Results.Add(@{test='actual_task_owned_sentinel_argv_stdout_stderr_repository';passed=$true;observed_owned_processes=$f15Observed.Count;docker_command_observed=$true;docker_child_process_observed=$f15DockerChildObserved;local_tls_failure_expected=$true})
    $f15SafeProgress=@{synthetic_only=$true;tests=@($f15Results)} | ConvertTo-Json -Depth 10
    if ($f15SafeProgress.Contains($f15Sentinel)) { throw 'SENTINEL_IN_PROGRESS_JSON' }
} catch {
    Write-Output (@{failure_phase=$f15Evidence.failure_phase;failure_class=$f15Evidence.native_failure_class;native_exit=$f15Evidence.native_exit_code;fixed_error_type=$_.Exception.GetType().Name} | ConvertTo-Json -Compress)
    throw
} finally {
    $f15Listener.Stop()
    $f15NativeSecure=$null; $f15NativeBstr=[IntPtr]::Zero; $f15NativeUrl=$null
    $f15NativeCleanup=Invoke-F15IndependentCleanup ([ordered]@{
        dump_clients={
            $f15ClientFailures=0
            foreach ($p in @($f15CliChild,$f15ShellChild)) {
                if ($p) {
                    try { Stop-F15OwnedProcess $p; if (-not $p.HasExited) { throw 'NATIVE_CHILD_STILL_ALIVE' }; $p.Dispose() }
                    catch { $f15ClientFailures++ }
                }
            }
            if ($f15Proxy) {
                $ids=Invoke-F15Native $f15Docker @('ps','-aq','--filter',('label=glamgenius.f15.run='+$f15Proxy.Token))
                Assert-F15NativeSuccess $ids 'synthetic_client_cleanup'
                foreach ($id in ($ids.Output -split "`r?`n" | Where-Object { $_ -match '^[a-f0-9]{12,64}$' })) {
                    try { Assert-F15NativeSuccess (Invoke-F15Native $f15Docker @('rm','-f',$id)) 'synthetic_client_cleanup' }
                    catch { $f15ClientFailures++ }
                }
            }
            if ($f15ClientFailures) { throw 'SYNTHETIC_CLIENT_CLEANUP_FAILED' }
        }
        local_container={};local_volume={};workspace={}
        sql_files={[IO.File]::Delete((Join-Path $f15Root 'synthetic-dump.sql'))}
        credential_process={
            Stop-F15CredentialLifetime -Password ([ref]$f15NativeSecure) -Bstr ([ref]$f15NativeBstr) -Plaintext ([ref]$f15Sentinel) -SafeDumpUrl ([ref]$f15NativeUrl) -Proxy ([ref]$f15Proxy) -Evidence $f15Evidence
        }
    })
    if (-not $f15NativeCleanup.Success) { throw 'NATIVE_SAFETY_CLEANUP_FAILED' }
}
$f15Report=@{synthetic_only=$true;production_connection=$false;tests=@($f15Results);sentinel_retained=$false;secret_values_printed=$false}
$f15Json=$f15Report | ConvertTo-Json -Depth 10
[IO.File]::WriteAllText((Join-Path $f15Root 'native-safety-results.json'),$f15Json,[Text.UTF8Encoding]::new($false))
Write-Output ('Native safety tests passed: '+$f15Results.Count+'; synthetic local-only; no sentinel retained.')
