param([string]$Toolchain = '')

$ErrorActionPreference = 'Stop'
$compiler = if ($Toolchain) { Join-Path $Toolchain 'arm-none-eabi-gcc.exe' } else { 'arm-none-eabi-gcc' }
$objcopy = if ($Toolchain) { Join-Path $Toolchain 'arm-none-eabi-objcopy.exe' } else { 'arm-none-eabi-objcopy' }
$size = if ($Toolchain) { Join-Path $Toolchain 'arm-none-eabi-size.exe' } else { 'arm-none-eabi-size' }

Push-Location $PSScriptRoot
try {
    New-Item -ItemType Directory -Force -Path build | Out-Null
    $flags = @('-mcpu=cortex-m4', '-mthumb', '-mfloat-abi=soft', '-O0', '-g3', '-fno-lto', '-ffreestanding', '-fno-builtin', '-ffunction-sections', '-fdata-sections', '-Wall', '-Wextra', '-Werror', '-std=c11', '-Ifirmware')
    $compileArgs = $flags + @('firmware/startup.s', 'firmware/main.c', 'firmware/clefia128.c', '-nostdlib', '-Tfirmware/stm32f415.ld', '-Wl,--gc-sections,-Map=build/clefia128.map', '-lgcc', '-o', 'build/clefia128.elf')
    & $compiler @compileArgs
    if ($LASTEXITCODE -ne 0) { throw "Compiler failed: $LASTEXITCODE" }
    & $objcopy -O binary build/clefia128.elf build/clefia128.bin
    if ($LASTEXITCODE -ne 0) { throw "objcopy failed: $LASTEXITCODE" }
    & $size build/clefia128.elf
    if ($LASTEXITCODE -ne 0) { throw "size failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}
