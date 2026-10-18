# PowerShell 5.1+ launcher. No installs, execution-policy changes, or cwd changes.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$forwarded = @($args)

function Quote-WindowsArgument([string] $Value) {
    # C-runtime quoting preserves empty arguments, embedded quotes, and trailing slashes.
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    return '"' + $escaped + '"'
}

function Invoke-PythonProcess([string] $Executable, [string[]] $Arguments, [bool] $Probe) {
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = (Get-Location).ProviderPath
    $info.EnvironmentVariables['PYTHON_MANAGER_AUTOMATIC_INSTALL'] = 'false'
    $info.EnvironmentVariables.Remove('PYLAUNCHER_ALLOW_INSTALL')
    $info.EnvironmentVariables.Remove('PYLAUNCHER_ALWAYS_INSTALL')
    if ($null -ne $info.PSObject.Properties['ArgumentList']) {
        foreach ($value in $Arguments) { $info.ArgumentList.Add($value) }
    } else {
        $info.Arguments = (($Arguments | ForEach-Object { Quote-WindowsArgument $_ }) -join ' ')
    }
    # Capture both streams: a hidden Windows process cannot reliably inherit
    # redirected console handles. Drain concurrently to avoid pipe deadlocks.
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $info.StandardErrorEncoding = New-Object System.Text.UTF8Encoding($false)
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void] $process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if ($Probe) {
            if (-not $process.WaitForExit(10000)) {
                $process.Kill()
                $process.WaitForExit()
                return 127
            }
            [void] $stdout.GetAwaiter().GetResult()
            [void] $stderr.GetAwaiter().GetResult()
        } else {
            $process.WaitForExit()
            [Console]::Out.Write($stdout.GetAwaiter().GetResult())
            [Console]::Error.Write($stderr.GetAwaiter().GetResult())
        }
        return $process.ExitCode
    } finally {
        $process.Dispose()
    }
}

function Find-Executable([string] $Name) {
    if ($Name.Contains('/') -or $Name.Contains('\') -or [IO.Path]::IsPathRooted($Name)) {
        # PowerShell's location can differ from the process working directory
        # after Set-Location. Resolve against that location before using .NET IO.
        $resolved = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Name)
        if ([IO.File]::Exists($resolved)) { return $resolved }
        return $null
    }
    $found = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($found) { return $found.Source }
    return $null
}

$candidates = @()
if ($env:PERSONA_REVIEW_PYTHON) {
    $candidates += @{ Name = $env:PERSONA_REVIEW_PYTHON; Prefix = @() }
} else {
    $candidates += @{ Name = 'py.exe'; Prefix = @('-3') }
    $candidates += @{ Name = 'python3.exe'; Prefix = @() }
    $candidates += @{ Name = 'python.exe'; Prefix = @() }
}
$probeCode = 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'
foreach ($candidate in $candidates) {
    try {
        $executable = Find-Executable $candidate.Name
        if (-not $executable) { continue }
        $probeArgs = @($candidate.Prefix) + @('-I', '-c', $probeCode)
        if ((Invoke-PythonProcess $executable $probeArgs $true) -ne 0) { continue }
    } catch { continue }
    $helperArgs = @($candidate.Prefix) + @('-I', (Join-Path $PSScriptRoot 'persona_review.py')) + $forwarded
    try { exit (Invoke-PythonProcess $executable $helperArgs $false) }
    catch {
        [Console]::Error.WriteLine('persona-review: Could not start the selected Python executable.')
        exit 126
    }
}
[Console]::Error.WriteLine('persona-review: Python 3.10+ was not found or could not run.')
[Console]::Error.WriteLine('Install Python from https://www.python.org/downloads/ and reopen your terminal.')
[Console]::Error.WriteLine('Or set PERSONA_REVIEW_PYTHON to one trusted executable path (no extra arguments).')
[Console]::Error.WriteLine('No Python installation or configuration write was performed.')
exit 127
