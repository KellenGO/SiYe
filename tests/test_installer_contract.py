from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_installer_is_per_user_upgradeable_and_preserves_user_data():
    script = (ROOT / "installer" / "SiYe.iss").read_text(encoding="utf-8")
    assert "AppId={{FBEA870E-53EB-4CD9-AE13-B17040209793}" in script
    assert "DefaultDirName={localappdata}\\Programs\\SiYe" in script
    assert "PrivilegesRequired=lowest" in script
    assert "ArchitecturesAllowed=x64compatible" in script
    assert "UninstallDisplayIcon={app}\\{#MyAppExeName}" in script
    assert "[UninstallDelete]" not in script
    assert "browser_data" not in script and "\\data\\" not in script


def test_installer_has_standard_shortcuts_and_safe_post_install_launch():
    script = (ROOT / "installer" / "SiYe.iss").read_text(encoding="utf-8")
    assert 'Name: "{group}\\{#MyAppName}"' in script
    assert 'Name: "{userdesktop}\\{#MyAppName}"' in script
    assert 'Tasks: desktopicon' in script
    assert 'Flags: nowait postinstall skipifsilent' in script
    assert 'WorkingDir: "{app}"' in script


def test_simplified_chinese_translation_is_vendored_with_its_license():
    script = (ROOT / "installer" / "SiYe.iss").read_text(encoding="utf-8")
    assert 'MessagesFile: "languages\\ChineseSimplified.isl"' in script
    assert (ROOT / "installer" / "languages" / "ChineseSimplified.isl").is_file()
    assert (ROOT / "installer" / "languages" / "LICENSE").is_file()


def test_release_publishes_installer_as_primary_asset_and_keeps_portable_zip():
    workflow = (ROOT / ".github" / "workflows" / "release-package.yml").read_text(encoding="utf-8")
    setup = "dist/SiYe-Setup-Windows-x64.exe"
    portable = "dist/SiYe-Windows-x64.zip"
    assert "choco install innosetup" in workflow
    assert "scripts/installer_clean_room_smoke.py" in workflow
    release_step = workflow[workflow.index("- name: Publish tagged GitHub Release"):]
    assert release_step.index(setup) < release_step.index(portable)
    assert f"{setup}.sha256" in workflow
    assert f"{portable}.sha256" in workflow


def test_build_pipeline_validates_payload_before_compiling_installer():
    build = (ROOT / "scripts" / "build_installer.ps1").read_text(encoding="utf-8")
    assert "scripts/package_exe.py" in build
    assert '"--validate-only"' in build
    assert "INNO_SETUP_COMPILER" in build
    assert "Get-FileHash" in build
    assert '$checksumPath = "$installerPath.sha256"' in build


def test_installer_smoke_uses_an_isolated_runtime_port():
    smoke = (ROOT / "scripts" / "installer_clean_room_smoke.py").read_text(encoding="utf-8")
    exe_smoke = (ROOT / "scripts" / "exe_clean_room_smoke.py").read_text(encoding="utf-8")
    assert 'listener.bind(("127.0.0.1", 0))' in smoke
    assert '"--port", str(port)' in smoke
    assert 'parser.add_argument("--port", type=int, default=8080)' in exe_smoke
    assert 'env["SIYE_PORT"] = str(port)' in exe_smoke
