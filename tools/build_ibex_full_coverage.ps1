param(
  [string]$VerilatorRoot = 'D:\verilator-5.050',
  [string]$MsysRoot = 'C:\msys64',
  [string]$BuildRoot = 'D:\tmp-llm4dv',
  [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$ibexRoot = Join-Path $repoRoot 'ibex_cpu'
$objDir = Join-Path $BuildRoot 'obj_ibex_cov'
$tempDir = Join-Path $BuildRoot 'tmp'
$verilator = Join-Path $VerilatorRoot 'bin\verilator.exe'
$make = Join-Path $MsysRoot 'usr\bin\make.exe'
$gxx = Join-Path $MsysRoot 'ucrt64\bin\g++.exe'
$ar = Join-Path $MsysRoot 'ucrt64\bin\ar.exe'

foreach ($required in @($verilator, $make, $gxx, $ar, (Join-Path $ibexRoot 'Makefile'))) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "Missing build dependency: $required"
  }
}

New-Item -ItemType Directory -Force $BuildRoot, $tempDir | Out-Null
if ($Clean -and (Test-Path -LiteralPath $objDir)) {
  $resolvedBuild = [IO.Path]::GetFullPath($BuildRoot)
  $resolvedObj = [IO.Path]::GetFullPath($objDir)
  if (-not $resolvedObj.StartsWith($resolvedBuild + [IO.Path]::DirectorySeparatorChar)) {
    throw "Refusing to clean an object directory outside BuildRoot: $resolvedObj"
  }
  Remove-Item -LiteralPath $resolvedObj -Recurse -Force
}

$drive = 'I:'
$existing = (& subst) | Where-Object { $_ -match '^I:\\:' }
if ($existing) {
  $mapped = (($existing -split '=>', 2)[1]).Trim()
  if ([IO.Path]::GetFullPath($mapped) -ne [IO.Path]::GetFullPath($ibexRoot)) {
    throw "I: is already mapped to $mapped; unmap it or change the build script drive."
  }
} else {
  & subst $drive $ibexRoot
  if ($LASTEXITCODE -ne 0) { throw 'Unable to create the I: source mapping.' }
}

$sources = @()
foreach ($line in Get-Content (Join-Path $ibexRoot 'Makefile')) {
  foreach ($match in [regex]::Matches($line, '\$\(PWD\)/([^\s\\]+\.(?:sv|vlt))')) {
    $sources += 'I:\' + ($match.Groups[1].Value -replace '/', '\')
  }
}
if ($sources.Count -ne 106) {
  throw "Expected 106 Makefile source/control entries, found $($sources.Count)."
}

$includes = @(
  'lowrisc_dv_crypto_prince_ref_0.1', 'lowrisc_dv_dv_fcov_macros_0',
  'lowrisc_dv_secded_enc_0', 'lowrisc_prim_util_get_scramble_params_0\rtl',
  'lowrisc_prim_util_memload_0\rtl', 'lowrisc_dv_scramble_model_0',
  'lowrisc_dv_verilator_memutil_dpi_0\cpp',
  'lowrisc_dv_verilator_memutil_dpi_scrambled_0\cpp',
  'lowrisc_prim_assert_0.1\rtl', 'lowrisc_ibex_ibex_core_0.1\rtl'
) | ForEach-Object { '-II:\src\' + $_ }

$arguments = @(
  '--cc', '--exe', '--top-module', 'ibex_coverage_top', '--Mdir', $objDir,
  '-DSYNTHESIS=1', '-DRVFI=1', '-Wno-fatal', '--coverage', '--coverage-expr',
  '--coverage-underscore', '--coverage-per-instance', '-CFLAGS', '-std=c++17 -O2'
) + $includes + $sources + @(
  'I:\tools\ibex_coverage_top.sv', 'I:\tools\ibex_coverage_main.cpp'
)

$env:VERILATOR_ROOT = $VerilatorRoot
$env:TEMP = $tempDir
$env:TMP = $tempDir
$env:PATH = "$(Join-Path $MsysRoot 'ucrt64\bin');$(Join-Path $MsysRoot 'usr\bin');$(Join-Path $VerilatorRoot 'bin');$env:PATH"
& $verilator @arguments
if ($LASTEXITCODE -ne 0) { throw "Verilator elaboration failed with exit code $LASTEXITCODE." }

& $make -C $objDir -f Vibex_coverage_top.mk -j 24 `
  "CXX=$($gxx -replace '\\', '/')" "LINK=$($gxx -replace '\\', '/')" "AR=$($ar -replace '\\', '/')"
if ($LASTEXITCODE -ne 0) { throw "C++ build failed with exit code $LASTEXITCODE." }

$simulator = Join-Path $objDir 'Vibex_coverage_top.exe'
if (-not (Test-Path -LiteralPath $simulator)) { throw "Simulator was not produced: $simulator" }
Write-Output "Built full Ibex coverage simulator: $simulator"
