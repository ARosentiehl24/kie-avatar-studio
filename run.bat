@echo off
REM Lanza Kie Avatar Studio en Windows. Bootstrap idempotente:
REM   - Crea .venv si no existe
REM   - Instala deps de requirements.txt
REM   - Copia .env.example -> .env si falta
REM   - Ejecuta `python -m kie_avatar_studio`
REM
REM Uso:
REM   run.bat                 -> lanza la TUI
REM   run.bat --reinstall     -> fuerza reinstalacion de deps
REM   run.bat --dev           -> instala extras [dev] (pytest, ruff, mypy)

setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VENV_DIR=.venv"
set "PY_BIN=python"
set "REINSTALL=0"
set "DEV=0"

:parse_args
if "%~1"=="" goto args_done
if /I "%~1"=="--reinstall" ( set "REINSTALL=1" & shift & goto parse_args )
if /I "%~1"=="--dev"       ( set "DEV=1" & shift & goto parse_args )
if /I "%~1"=="-h"          ( goto show_help )
if /I "%~1"=="--help"      ( goto show_help )
echo Opcion desconocida: %~1
exit /b 2

:show_help
echo run.bat [--reinstall] [--dev]
exit /b 0

:args_done

where %PY_BIN% >nul 2>&1
if errorlevel 1 (
  echo [X] No se encontro 'python' en PATH. Instala Python 3.11+ desde python.org.
  exit /b 1
)

for /f "delims=" %%V in ('%PY_BIN% -c "import sys; print(1 if sys.version_info[:2] >= (3,11) else 0)"') do set "PY_OK=%%V"
if not "%PY_OK%"=="1" (
  echo [X] Se requiere Python 3.11+.
  %PY_BIN% --version
  exit /b 1
)

if not exist "%VENV_DIR%" (
  echo [+] Creando entorno virtual en %VENV_DIR% ...
  %PY_BIN% -m venv "%VENV_DIR%"
  set "REINSTALL=1"
)

call "%VENV_DIR%\Scripts\activate.bat"

set "STAMP=%VENV_DIR%\.deps.stamp"
set "NEED_INSTALL=0"
if "%REINSTALL%"=="1" set "NEED_INSTALL=1"
if not exist "%STAMP%" set "NEED_INSTALL=1"
if exist "requirements.txt" if exist "%STAMP%" (
  for %%F in ("requirements.txt") do set "REQ_TIME=%%~tF"
  for %%F in ("%STAMP%")        do set "STAMP_TIME=%%~tF"
  if "!REQ_TIME!" gtr "!STAMP_TIME!" set "NEED_INSTALL=1"
)
if exist "pyproject.toml" if exist "%STAMP%" (
  for %%F in ("pyproject.toml") do set "TOML_TIME=%%~tF"
  for %%F in ("%STAMP%")        do set "STAMP_TIME=%%~tF"
  if "!TOML_TIME!" gtr "!STAMP_TIME!" set "NEED_INSTALL=1"
)

if "%NEED_INSTALL%"=="1" (
  echo [+] Instalando dependencias ...
  python -m pip install --upgrade pip >nul
  pip install -r requirements.txt
  if "%DEV%"=="1" (
    pip install -e ".[dev]"
  ) else (
    pip install -e . >nul
  )
  if errorlevel 1 (
    echo [X] Fallo la instalacion de dependencias.
    exit /b 1
  )
  type nul > "%STAMP%"
)

if not exist ".env" (
  echo [+] .env no existe -- copiando desde .env.example.
  copy /Y ".env.example" ".env" >nul
  echo     [!] Edita .env y completa KIE_API_KEY antes de crear jobs reales.
)

echo [+] Lanzando Kie Avatar Studio ...
python -m kie_avatar_studio
exit /b %ERRORLEVEL%
