param([Parameter(Mandatory=$true)][string]$PythonPath,
      [Parameter(Mandatory=$true)][string]$EvidenceDirectory,
      [switch]$InjectEarlyDisposedMutant)
$ErrorActionPreference='Stop'
$f15Root=[IO.Path]::GetFullPath($EvidenceDirectory)
[void][IO.Directory]::CreateDirectory($f15Root)
$f15Operator=Join-Path $PSScriptRoot 'Invoke-StoreB-Drill.ps1'
$f15Tokens=$null; $f15Errors=$null
$f15Ast=[Management.Automation.Language.Parser]::ParseFile($f15Operator,[ref]$f15Tokens,[ref]$f15Errors)
if ($f15Errors.Count) { throw 'OPERATOR_PARSE_FAILED' }
$f15Def=$f15Ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'ConvertTo-F15NativeArgument'},$true)
Invoke-Expression $f15Def.Extent.Text
. (Join-Path $PSScriptRoot 'Review-Safety.ps1')
# Execute the actual operator transition, rather than a test-only copy of the
# disposal sequence. No source connection, SQL dump or local restore is run.
$f15Source=[IO.File]::ReadAllText($f15Operator)
$f15Transition=[regex]::Match($f15Source,'(?ms)^    if \(\(Get-F15ComparableManifest.*?(?=^    \$f15Evidence.dump_coverage)').Value
if (-not $f15Transition -or $f15Transition -notmatch 'Stop-F15CredentialLifetime') { throw 'CREDENTIAL_TRANSITION_MISSING' }
function Get-F15ComparableManifest($Manifest,$Source) { return ($Manifest | ConvertTo-Json -Compress) }
function Clear-F15OwnedDumpClients { $f15Observation.client_cleanup_attempts++ }
function Write-F15Progress($Phase) { $f15Observation.phases.Add($Phase) }
$f15Results=[Collections.Generic.List[object]]::new()
$f15Cases=@('graceful','stdin_close_failure','graceful_timeout','graceful_owned_child')
if ($InjectEarlyDisposedMutant) { $f15Cases=@('early_disposed_mutant') }
foreach ($f15Case in $f15Cases) {
    $f15Password=$null; $f15SecretPointer=[IntPtr]::Zero; $f15Plaintext=$null; $f15SafeDumpUrl=$null; $f15Proxy=$null
    $f15SecureWitness=$null; $f15OwnedId=$null; $f15ChildIds=@(); $f15Fixture=$null
    $f15Evidence=[ordered]@{credential_disposed=$false}
    $f15Observation=@{mode=$f15Case;early_claim=$false;client_cleanup_attempts=0;phases=[Collections.Generic.List[string]]::new()}
    $f15Before=[pscustomobject]@{server_version='17.6';synthetic_digest=('a'*64)}; $f15After=$f15Before
    $f15Caught=$null; $f15MutantDetected=$false
    try {
        $f15ProxyScript=Join-Path $PSScriptRoot 'pinned-docker-proxy.py'
        if ($f15Case -eq 'graceful_owned_child') {
            # The real proxy plus one deliberately orphan-prone synthetic
            # credential child; password stays exclusively in its environment.
            $f15Fixture=Join-Path $f15Root 'synthetic-owned-child-proxy.py'
            $f15ProxyCode=[IO.File]::ReadAllText($f15ProxyScript)
            $f15Needle="                sys.stdout.write('CREDENTIAL_CHANNEL_READY\n')"
            $f15Injected="                import os, subprocess`n                synthetic_child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env=dict(os.environ, PGPASSWORD=state.password), stdin=subprocess.DEVNULL)`n                sys.stdout.write(json.dumps({'synthetic_child_pid': synthetic_child.pid})+'\n')`n"+$f15Needle
            if (-not $f15ProxyCode.Contains($f15Needle)) { throw 'SYNTHETIC_CHILD_INJECTION_MISSING' }
            [IO.File]::WriteAllText($f15Fixture,$f15ProxyCode.Replace($f15Needle,$f15Injected),[Text.UTF8Encoding]::new($false))
            $f15ProxyScript=$f15Fixture
        }
        $f15Start=[Diagnostics.ProcessStartInfo]::new()
        $f15Start.FileName=[IO.Path]::GetFullPath($PythonPath)
        $f15Start.Arguments=(@($f15ProxyScript,'--pipe','\\.\pipe\dockerDesktopLinuxEngine','--tag','synthetic-lifecycle','--image',('sha256:'+('a'*64))) | ForEach-Object { ConvertTo-F15NativeArgument $_ }) -join ' '
        $f15Start.UseShellExecute=$false; $f15Start.CreateNoWindow=$true
        $f15Start.RedirectStandardInput=$true; $f15Start.RedirectStandardOutput=$true; $f15Start.RedirectStandardError=$true
        $f15Start.EnvironmentVariables.Remove('PGPASSWORD'); $f15Start.EnvironmentVariables.Remove('PGPASSFILE')
        $f15Process=[Diagnostics.Process]::Start($f15Start)
        $f15Proxy=[pscustomobject]@{Process=$f15Process;Channel=$f15Process.StandardInput}
        $f15OwnedId=$f15Process.Id
        $f15Ready=$f15Process.StandardOutput.ReadLineAsync()
        if (-not $f15Ready.Wait(15000) -or -not ($f15Ready.Result | ConvertFrom-Json).token) { throw 'SYNTHETIC_PROXY_NOT_READY' }
        $f15Secret=[Guid]::NewGuid().ToString('N')+[Guid]::NewGuid().ToString('N')
        $f15Password=ConvertTo-SecureString $f15Secret -AsPlainText -Force
        $f15SecureWitness=$f15Password
        $f15SecretPointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($f15Password)
        $f15Plaintext=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($f15SecretPointer)
        $f15SafeDumpUrl='postgresql://synthetic@localhost/postgres'
        $f15Process.StandardInput.WriteLine((@{password=$f15Plaintext} | ConvertTo-Json -Compress)); $f15Process.StandardInput.Flush()
        if ($f15Case -eq 'graceful_owned_child') {
            $f15ChildAck=$f15Process.StandardOutput.ReadLineAsync()
            if (-not $f15ChildAck.Wait(15000)) { throw 'SYNTHETIC_CHILD_NOT_READY' }
            $f15InjectedChild=($f15ChildAck.Result | ConvertFrom-Json).synthetic_child_pid
            if (-not $f15InjectedChild -or -not (Get-CimInstance Win32_Process -Filter ('ProcessId='+$f15InjectedChild) -Property ProcessId,ParentProcessId)) { throw 'SYNTHETIC_OWNED_CHILD_NOT_OBSERVED' }
            $f15ChildIds=@($f15InjectedChild)
        }
        $f15Ack=$f15Process.StandardOutput.ReadLineAsync()
        if (-not $f15Ack.Wait(15000) -or $f15Ack.Result -ne 'CREDENTIAL_CHANNEL_READY') { throw 'SYNTHETIC_CREDENTIAL_NOT_READY' }
        $f15Secret=$null
        $f15ChildIds+=@(Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$f15OwnedId) -Property ProcessId,ParentProcessId | ForEach-Object { $_.ProcessId })
        $f15Channel=[pscustomobject]@{Writer=$f15Proxy.Channel;Process=$f15Process;Observation=$f15Observation;Evidence=$f15Evidence}
        $f15Channel | Add-Member ScriptMethod Close {
            if ($this.Evidence.credential_disposed -and -not $this.Process.HasExited) { $this.Observation.early_claim=$true }
            if ($this.Observation.mode -eq 'stdin_close_failure') { throw 'INJECTED_STDIN_CLOSE_FAILURE' }
            if ($this.Observation.mode -eq 'graceful_timeout') { return } # Real pipe remains open; real proxy retains its credential.
            $this.Writer.Close()
        }
        $f15Proxy.Channel=$f15Channel
        $f15Executed=$f15Transition
        if ($InjectEarlyDisposedMutant) {
            $f15Executed=[regex]::Replace($f15Transition,'(?m)^    Stop-F15CredentialLifetime[^\r\n]*','    $f15Evidence.credential_disposed = $true')
        }
        try { Invoke-Expression $f15Executed } catch { $f15Caught=$_.Exception.Message }
        if ($InjectEarlyDisposedMutant) {
            $f15MutantDetected=($f15Caught -eq 'CREDENTIAL_RESTORE_BOUNDARY_REJECTED' -and -not $f15Process.HasExited -and $f15Observation.phases.Count -eq 0)
            if (-not $f15MutantDetected) { throw 'EARLY_DISPOSED_MUTANT_SURVIVED' }
        } else {
            $f15ExpectedFault=$f15Case -in @('stdin_close_failure','graceful_timeout')
            if (($f15ExpectedFault -and $f15Caught -ne 'CREDENTIAL_LIFETIME_CLEANUP_FAILED') -or (-not $f15ExpectedFault -and $f15Caught)) { throw 'CREDENTIAL_FAULT_CLASS_MISMATCH' }
            if (-not $f15Evidence.credential_disposed -or -not $f15Evidence.credential_proxy_exit_verified -or
                -not $f15Evidence.credential_proxy_children_exit_verified -or $null -ne $f15Proxy -or
                $null -ne $f15Password -or $f15SecretPointer -ne [IntPtr]::Zero -or $null -ne $f15Plaintext -or
                $null -ne $f15SafeDumpUrl -or $f15Observation.early_claim) { throw 'CREDENTIAL_DESTRUCTION_NOT_PROVEN' }
            $f15Disposed=$false
            try { $f15ForbiddenCopy=$f15SecureWitness.Copy(); $f15ForbiddenCopy.Dispose() } catch { $f15Disposed=$true }
            if (-not $f15Disposed) { throw 'SECURE_STRING_NOT_DISPOSED' }
            $f15DestructionTime=$f15Evidence.credential_destroyed_at_utc
            # Defensive callback uses the same helper and is genuinely idempotent.
            $f15Cleanup=Invoke-F15IndependentCleanup ([ordered]@{dump_clients={};local_container={};local_volume={};sql_files={};workspace={};credential_process={
                Stop-F15CredentialLifetime -Password ([ref]$f15Password) -Bstr ([ref]$f15SecretPointer) -Plaintext ([ref]$f15Plaintext) -SafeDumpUrl ([ref]$f15SafeDumpUrl) -Proxy ([ref]$f15Proxy) -Evidence $f15Evidence -DumpClientCleanup { Clear-F15OwnedDumpClients }
            }})
            if (-not $f15Cleanup.Success -or $f15Evidence.credential_destroyed_at_utc -ne $f15DestructionTime -or $f15Observation.client_cleanup_attempts -lt 2) { throw 'CREDENTIAL_CLEANUP_NOT_IDEMPOTENT' }
        }
    } finally {
        # Even a failed assertion/mutant must destroy its actual synthetic proxy.
        try { Stop-F15CredentialLifetime -Password ([ref]$f15Password) -Bstr ([ref]$f15SecretPointer) -Plaintext ([ref]$f15Plaintext) -SafeDumpUrl ([ref]$f15SafeDumpUrl) -Proxy ([ref]$f15Proxy) -Evidence $f15Evidence }
        catch { if ($null -ne $f15Proxy -or -not $f15Evidence.credential_disposed) { throw 'SYNTHETIC_PROCESS_CLEANUP_FAILED' } }
        $f15Secret=$null; $f15SecureWitness=$null
        if ($f15Fixture) { [IO.File]::Delete($f15Fixture) }
    }
    foreach ($f15Identity in @($f15OwnedId)+$f15ChildIds) {
        if (Get-CimInstance Win32_Process -Filter ('ProcessId='+$f15Identity) -Property ProcessId) { throw 'SYNTHETIC_OWNED_PROCESS_REMAINS' }
    }
    $f15Results.Add(@{test=$f15Case;passed=$true;actual_proxy_exited=$true;owned_children_absent=$true;secure_string_disposed=$true;bstr_zero_freed=$true;plaintext_nulled=$true;process_disposed_after_exit=$true;mutant_executed=[bool]$InjectEarlyDisposedMutant;mutant_killed=$f15MutantDetected;cleanup_errors=@($f15Evidence.credential_cleanup_errors)})
}
$f15Report=@{synthetic_only=$true;production_connection=$false;actual_operator_transition_executed=$true;tests=@($f15Results);secret_values_printed=$false;secret_retained=$false}
$f15Name=if ($InjectEarlyDisposedMutant) {'credential-lifecycle-mutant.json'} else {'credential-lifecycle-results.json'}
[IO.File]::WriteAllText((Join-Path $f15Root $f15Name),($f15Report | ConvertTo-Json -Depth 10),[Text.UTF8Encoding]::new($false))
Write-Output ('Credential lifetime tests passed: '+$f15Results.Count+'; actual synthetic proxies destroyed; no production access.')
