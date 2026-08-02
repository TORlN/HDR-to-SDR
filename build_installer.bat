@echo off
setlocal enabledelayedexpansion

:: Resolve repo root (directory containing this script, no trailing backslash)
set "REPO_ROOT=%~dp0"
if "%REPO_ROOT:~-1%"=="\" set "REPO_ROOT=%REPO_ROOT:~0,-1%"

set "ISCC=C:\Users\Torin\AppData\Local\Programs\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
set "DIST_DIR=%REPO_ROOT%\dist\HDR_to_SDR_Converter"
set "INSTALLER_OUT=%REPO_ROOT%\installer_output"
set "DLIB=%LOCALAPPDATA%\Microsoft\MicrosoftTrustedSigningClientTools\Azure.CodeSigning.Dlib.dll"
set "METADATA=%REPO_ROOT%\signing\metadata.json"

:: -- Step 0: Preflight ----------------------------------------------------------
echo.
echo [STEP 0] Preflight checks
if not exist "%REPO_ROOT%\src\ffmpeg.exe"  ( echo [ERROR] src\ffmpeg.exe not found & exit /b 1 )
if not exist "%REPO_ROOT%\src\ffprobe.exe" ( echo [ERROR] src\ffprobe.exe not found & exit /b 1 )
if not exist "%REPO_ROOT%\logo\icon.ico"   ( echo [ERROR] logo\icon.ico not found & exit /b 1 )
if not exist "%REPO_ROOT%\src\luts\rec2020_to_rec709.cube" ( echo [ERROR] src\luts\rec2020_to_rec709.cube not found & exit /b 1 )
if not exist "%REPO_ROOT%\installer.iss"   ( echo [ERROR] installer.iss not found & exit /b 1 )

:: -- Pro backend detection ------------------------------------------------------
set "FREE_BUILD="
if not exist "%REPO_ROOT%\src\pro\licensing.py" (
    echo.
    echo ================================================================
    echo   WARNING: src\pro\ NOT FOUND -- this will be a FREE-ONLY build.
    echo   Pro licensing, activation UI, and Batch Queue will be ABSENT.
    echo   Every user gets the Community Edition. Do NOT ship as a release.
    echo ================================================================
    echo.
    SET /P FREE_CONFIRM=Type FREE to build anyway, anything else aborts:
    if /I not "!FREE_CONFIRM!"=="FREE" goto :free_build_aborted
    set "FREE_BUILD=1"
    echo [WARN] Proceeding with a FREE-ONLY build at your explicit confirmation.
) else (
    echo [OK] Pro backend present: src\pro\licensing.py
)
goto :free_build_resolved
:free_build_aborted
echo [ABORTED] Pro backend missing. Clone the private repo into src\pro\ first.
exit /b 1
:free_build_resolved

if defined FREE_BUILD (
    set "SETUP_EXE=%INSTALLER_OUT%\HDR_to_SDR_Setup_FREE.exe"
) else (
    set "SETUP_EXE=%INSTALLER_OUT%\HDR_to_SDR_Setup.exe"
)

if exist "%ISCC%" goto :iscc_ok
echo [ERROR] ISCC.exe not found. Install Inno Setup 6 or 7 from https://jrsoftware.org/isdl.php
exit /b 1
:iscc_ok

:: -- Signing choice --------------------------------------------------------------
echo.
SET /P SIGN_BUILD=Sign executables with Azure Trusted Signing? [Y/N]:
IF /I "!SIGN_BUILD!"=="Y" ( SET "SIGN_BUILD=Y" ) ELSE ( SET "SIGN_BUILD=N" )

IF /I "!SIGN_BUILD!"=="Y" (
    if not exist "%DLIB%" (
        echo [ERROR] Azure Trusted Signing dlib not found: %DLIB%
        echo         Run: winget install Microsoft.Azure.TrustedSigningClientTools
        exit /b 1
    )
    if not exist "%METADATA%" (
        echo [ERROR] signing\metadata.json not found -- fill in Endpoint and CodeSigningAccountName
        exit /b 1
    )
    where signtool >nul 2>&1
    if not errorlevel 1 ( set "SIGNTOOL=signtool" )
    if not defined SIGNTOOL (
        for /f "delims=" %%f in ('dir /b /ad "%ProgramFiles(x86)%\Windows Kits\10\bin" 2^>nul') do (
            if exist "%ProgramFiles(x86)%\Windows Kits\10\bin\%%f\x64\signtool.exe" set "SIGNTOOL=%ProgramFiles(x86)%\Windows Kits\10\bin\%%f\x64\signtool.exe"
        )
    )
    if not defined SIGNTOOL ( echo [ERROR] signtool.exe not found -- install Windows 10/11 SDK & exit /b 1 )
    echo [OK] signtool: !SIGNTOOL!
)
echo [OK] All prerequisites present

:: -- Step 1: Activate venv ------------------------------------------------------
echo.
echo [STEP 1] Activating virtual environment
if not exist "%REPO_ROOT%\.venv\Scripts\activate.bat" (
    echo [ERROR] .venv not found -- run: python -m venv .venv ^&^& pip install -r requirements.txt
    exit /b 1
)
call "%REPO_ROOT%\.venv\Scripts\activate.bat"
if errorlevel 1 ( echo [ERROR] Failed to activate .venv & exit /b 1 )
for /f "tokens=*" %%v in ('python --version 2^>^&1') do echo [OK] venv active: %%v

:: -- Step 1.5: Test suite gate ---------------------------------------------------
echo.
echo [STEP 1.5] Running test suite
if /I "%~1"=="--skip-tests" (
    set "TESTS_SKIPPED=1"
    echo [WARN] --skip-tests passed -- SKIPPING the test suite.
    goto :tests_done
)
set "TEST_LOG=%REPO_ROOT%\build_test_run.log"
if exist "%TEST_LOG%" del /q "%TEST_LOG%"

python -m unittest discover -s test -p "*_test.py" -t . > "%TEST_LOG%" 2>&1
set "PUBLIC_RC=!errorlevel!"

set "PRIVATE_RC=0"
if exist "%REPO_ROOT%\src\pro\test" (
    python -m unittest discover -s src/pro/test -p "*_test.py" -t . >> "%TEST_LOG%" 2>&1
    set "PRIVATE_RC=!errorlevel!"
)

if !PUBLIC_RC! neq 0 goto :tests_failed
if !PRIVATE_RC! neq 0 goto :tests_failed

for /f "tokens=*" %%s in ('findstr /r /c:"^Ran [0-9]* test" "%TEST_LOG%"') do echo [OK] %%s
echo [OK] All tests passed
goto :tests_done

:tests_failed
echo.
echo [ERROR] Test suite FAILED -- installer NOT built.
echo.
echo   Failing tests:
findstr /r /c:"^FAIL:" /c:"^ERROR:" "%TEST_LOG%"
echo.
findstr /r /c:"^Ran [0-9]* test" /c:"^FAILED" "%TEST_LOG%"
echo.
echo   Full log: %TEST_LOG%
echo.
echo   Fix the failures, or re-run with --skip-tests to bypass (NOT for releases).
exit /b 1

:tests_done

:: -- Step 2: Clean previous onedir output ---------------------------------------
echo.
echo [STEP 2] Cleaning previous dist and build output
if exist "%DIST_DIR%" (
    rmdir /s /q "%DIST_DIR%"
    if errorlevel 1 ( echo [ERROR] Failed to remove %DIST_DIR% & exit /b 1 )
)
if exist "%REPO_ROOT%\build" (
    rmdir /s /q "%REPO_ROOT%\build"
    if errorlevel 1 ( echo [ERROR] Failed to remove build\ & exit /b 1 )
)
echo [OK] Cleaned dist and build cache

:: -- Step 3: PyInstaller onedir build -------------------------------------------
:: The --hidden-import flags below (CLI branch) and the hiddenimports list in
:: HDR_to_SDR_Converter.spec (PyArmor branch) must name the same pro.* modules --
:: the PyArmor branch uses the .spec instead of these CLI flags entirely, so a
:: module added to one and not the other ships fine unobfuscated but silently
:: drops out of obfuscated builds. Keep both lists in sync.
echo.
echo [STEP 3] Running PyInstaller (--onedir)
if exist "%REPO_ROOT%\_obf\main.pyw" (
    echo [INFO] PyArmor obfuscated source detected -- using spec file
    python -m PyInstaller ^
        --distpath "%REPO_ROOT%\dist" ^
        --workpath "%REPO_ROOT%\build" ^
        "%REPO_ROOT%\HDR_to_SDR_Converter.spec"
) else (
    echo [INFO] Building from src\main.pyw
    python -m PyInstaller ^
        --onedir ^
        --windowed ^
        --name "HDR_to_SDR_Converter" ^
        --icon "%REPO_ROOT%\logo\icon.ico" ^
        --add-binary "%REPO_ROOT%\src\ffmpeg.exe;." ^
        --add-binary "%REPO_ROOT%\src\ffprobe.exe;." ^
        --add-data "%REPO_ROOT%\logo\icon.ico;." ^
        --add-data "%REPO_ROOT%\src\luts;luts" ^
        --add-data "%REPO_ROOT%\.venv\Lib\site-packages\tkinterdnd2;tkinterdnd2" ^
        --add-data "%REPO_ROOT%\.venv\Lib\site-packages\PIL;PIL" ^
        --hidden-import pro.licensing ^
        --hidden-import pro.batch ^
        --hidden-import pro.license_dialog ^
        --hidden-import pro._secrets ^
        --exclude-module numpy ^
        --exclude-module pandas ^
        --exclude-module scipy ^
        --exclude-module matplotlib ^
        --distpath "%REPO_ROOT%\dist" ^
        --workpath "%REPO_ROOT%\build" ^
        "%REPO_ROOT%\src\main.pyw"
)
if errorlevel 1 ( echo [ERROR] PyInstaller failed & exit /b 1 )
if not exist "%DIST_DIR%\HDR_to_SDR_Converter.exe" (
    echo [ERROR] PyInstaller finished but %DIST_DIR%\HDR_to_SDR_Converter.exe was not created
    exit /b 1
)
echo [OK] PyInstaller build complete: %DIST_DIR%

:: -- Bundle verification: confirm Pro modules actually reached the bundle -------
:: A missing --hidden-import (either branch above) makes PyInstaller succeed and
:: produce a completely normal-looking build with Pro code silently absent. The
:: bundled pure-Python modules never appear as loose files in dist\ (PyInstaller
:: archives them into the PYZ, which is embedded in the EXE) -- the only on-disk
:: record of what actually got bundled is the workpath's PYZ-00.toc, which lists
:: every embedded module as ('modname', 'source.py', 'PYMODULE'). Both the CLI
:: branch (--name "HDR_to_SDR_Converter") and the .spec branch (workpath subdir
:: takes the spec file's basename, "HDR_to_SDR_Converter.spec") write to the same
:: build\HDR_to_SDR_Converter\ subdirectory, so one path covers both branches.
:: Skipped entirely for a confirmed free build, where Pro modules are expected
:: to be absent.
if defined FREE_BUILD goto :pro_bundle_check_done
echo.
echo [CHECK] Verifying Pro modules reached the PyInstaller bundle
set "PYZ_TOC=%REPO_ROOT%\build\HDR_to_SDR_Converter\PYZ-00.toc"
set "PRO_BUNDLE_MISSING="
if not exist "%PYZ_TOC%" (
    set "PRO_BUNDLE_MISSING=1"
) else (
    for %%M in (pro.licensing pro.batch pro.license_dialog pro._secrets) do (
        findstr /c:"'%%M'" "%PYZ_TOC%" >nul
        if errorlevel 1 set "PRO_BUNDLE_MISSING=1"
    )
)
if defined PRO_BUNDLE_MISSING goto :pro_bundle_missing
echo [OK] Pro modules confirmed in bundle: pro.licensing, pro.batch, pro.license_dialog, pro._secrets
goto :pro_bundle_check_done

:pro_bundle_missing
echo.
echo [ERROR] Pro build, but the bundle does NOT contain the Pro package -- installer NOT built.
echo   src\pro\ is present and the test gate passed, but PyInstaller did not embed
echo   pro.licensing / pro.batch / pro.license_dialog / pro._secrets into this build.
echo   Left unbuilt, this would have shipped a Pro-labeled installer with no Pro
echo   features and no error anywhere in the chain.
echo   Checked: %PYZ_TOC%
echo   Check the --hidden-import flags in build_installer.bat (Step 3, CLI branch)
echo   and the hiddenimports list in HDR_to_SDR_Converter.spec (PyArmor branch).
exit /b 1

:pro_bundle_check_done

:: -- Step 3.5: Sign the main EXE (optional) -------------------------------------
IF /I "!SIGN_BUILD!"=="Y" (
    echo.
    echo [STEP 3.5] Code-signing HDR_to_SDR_Converter.exe
    "!SIGNTOOL!" sign /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib "%DLIB%" /dmdf "%METADATA%" /v /debug "%DIST_DIR%\HDR_to_SDR_Converter.exe"
    if errorlevel 1 ( echo [ERROR] Signing HDR_to_SDR_Converter.exe failed & exit /b 1 )
    echo [OK] HDR_to_SDR_Converter.exe signed
)

:: -- Step 4: Compile Inno Setup installer ---------------------------------------
echo.
echo [STEP 4] Compiling Inno Setup installer
if not exist "%INSTALLER_OUT%" mkdir "%INSTALLER_OUT%"
if defined FREE_BUILD (
    "%ISCC%" /DFREE_BUILD=1 "%REPO_ROOT%\installer.iss"
) else (
    "%ISCC%" "%REPO_ROOT%\installer.iss"
)
if errorlevel 1 ( echo [ERROR] ISCC failed & exit /b 1 )
if not exist "%SETUP_EXE%" (
    echo [ERROR] ISCC finished but %SETUP_EXE% was not created
    exit /b 1
)
echo [OK] Installer ready: %SETUP_EXE%

:: -- Step 4.5: Sign the installer (optional) ------------------------------------
IF /I "!SIGN_BUILD!"=="Y" (
    echo.
    echo [STEP 4.5] Code-signing HDR_to_SDR_Setup.exe
    "!SIGNTOOL!" sign /fd SHA256 /tr http://timestamp.acs.microsoft.com /td SHA256 /dlib "%DLIB%" /dmdf "%METADATA%" /v /debug "%SETUP_EXE%"
    if errorlevel 1 ( echo [ERROR] Signing HDR_to_SDR_Setup.exe failed & exit /b 1 )
    echo [OK] HDR_to_SDR_Setup.exe signed
)

:: -- Done -----------------------------------------------------------------------
echo.
if defined FREE_BUILD (
    echo [DONE] *** FREE-ONLY build *** : %SETUP_EXE%
    echo        This installer has NO Pro features. Do NOT publish it as a release.
) else IF /I "!SIGN_BUILD!"=="Y" (
    echo [DONE] Signed installer ready: %SETUP_EXE%
) ELSE (
    echo [DONE] Unsigned installer ready: %SETUP_EXE%
    echo        Re-run and answer Y to sign before releasing.
)
if defined TESTS_SKIPPED (
    echo [WARN] Tests were SKIPPED for this build -- do not release it untested.
)
endlocal
exit /b 0
