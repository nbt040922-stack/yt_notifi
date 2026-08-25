import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
INSTALLER = ROOT / "installer"


def test_manifest_contains_only_local_client_components():
    manifest = json.loads((INSTALLER / "client-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["components"]) == {"yt_notifi", "ytdownload"}
    assert manifest["components"]["yt_notifi"]["port"] == 8787
    assert manifest["components"]["ytdownload"]["port"] == 8790
    assert all("127.0.0.1" in item["health"] for item in manifest["components"].values())


def test_package_excludes_processing_services():
    names = ["Setup-ContentOpsClient.ps1", "Update-ContentOpsClient.ps1", "Uninstall-ContentOpsClient.ps1"]
    names += [f"scripts/{name}" for name in ("Start-ContentOpsClient.ps1", "Stop-ContentOpsClient.ps1", "Restart-ContentOpsClient.ps1", "Status-ContentOpsClient.ps1", "Watch-ContentOpsClient.ps1", "Check-ContentOpsEnvironment.ps1", "Repair-ContentOpsEnvironment.ps1")]
    files = [(INSTALLER / name).read_text(encoding="utf-8", errors="ignore") for name in names]
    text = "\n".join(files)
    assert "8791" not in text
    assert "8792" not in text
    assert "8780" not in text


def test_lifecycle_scripts_exist_and_preserve_data_contract():
    names = {
        "Setup-ContentOpsClient.ps1",
        "Update-ContentOpsClient.ps1",
        "Uninstall-ContentOpsClient.ps1",
        "scripts/Start-ContentOpsClient.ps1",
        "scripts/Stop-ContentOpsClient.ps1",
        "scripts/Restart-ContentOpsClient.ps1",
        "scripts/Status-ContentOpsClient.ps1",
        "scripts/Watch-ContentOpsClient.ps1",
        "scripts/Check-ContentOpsEnvironment.ps1",
        "scripts/Repair-ContentOpsEnvironment.ps1",
    }
    assert all((INSTALLER / name).is_file() for name in names)
    uninstall = (INSTALLER / "Uninstall-ContentOpsClient.ps1").read_text(encoding="utf-8")
    assert "DataRoot" in uninstall


def test_user_facing_package_docs_and_lifecycle_wrappers_exist():
    assert (INSTALLER / "README-INSTALL.txt").is_file()
    for name in (
        "Start-ContentOpsClient.ps1",
        "Stop-ContentOpsClient.ps1",
        "Restart-ContentOpsClient.ps1",
        "Status-ContentOpsClient.ps1",
    ):
        wrapper = INSTALLER / name
        assert wrapper.is_file()
        assert "scripts" in wrapper.read_text(encoding="utf-8")


def test_readme_documents_actual_client_contract():
    readme = (INSTALLER / "README-INSTALL.txt").read_text(encoding="utf-8")
    for text in (
        "CÀI ĐẶT NHANH",
        "127.0.0.1:8787",
        "127.0.0.1:8790",
        "Update-ContentOpsClient.ps1",
        "Uninstall-ContentOpsClient.ps1",
        "C:\\ProgramData\\ContentOps\\Client\\logs",
        "Telegram",
    ):
        assert text in readme


def test_packaged_setup_and_update_use_package_local_scripts():
    setup = (INSTALLER / "Setup-ContentOpsClient.ps1").read_text(encoding="utf-8")
    update = (INSTALLER / "Update-ContentOpsClient.ps1").read_text(encoding="utf-8")
    assert "Test-Path (Join-Path $PSScriptRoot 'scripts')" in setup
    assert "Join-Path $PackageRoot 'Setup-ContentOpsClient.ps1'" in update


def test_target_package_uses_owned_runtimes_only():
    checker = (INSTALLER / "scripts/Check-ContentOpsEnvironment.ps1").read_text(encoding="utf-8")
    start = (INSTALLER / "scripts/Start-ContentOpsClient.ps1").read_text(encoding="utf-8")
    repair = (INSTALLER / "scripts/Repair-ContentOpsEnvironment.ps1").read_text(encoding="utf-8")
    assert "YTDOWNLOAD.exe" in checker
    assert "yt_notifi_bootstrap.exe" in checker
    assert "python -m venv" not in repair
    assert "python -m uvicorn" not in start
    assert "yt_notifi_bootstrap.exe" in start
    setup = (INSTALLER / "Setup-ContentOpsClient.ps1").read_text(encoding="utf-8")
    assert "config state logs" in setup


def test_readme_does_not_present_developer_tools_as_user_prerequisites():
    readme = (INSTALLER / "README-INSTALL.txt").read_text(encoding="utf-8")
    assert "Python 3 có lệnh" not in readme
    assert "Node.js" not in readme
    assert "pip" not in readme


def test_installer_separates_program_files_from_mutable_programdata():
    setup = (INSTALLER / "Setup-ContentOpsClient.ps1").read_text(encoding="utf-8")
    start = (INSTALLER / "scripts/Start-ContentOpsClient.ps1").read_text(encoding="utf-8")
    uninstall = (INSTALLER / "Uninstall-ContentOpsClient.ps1").read_text(encoding="utf-8")
    assert "ProgramFiles" in setup
    assert "DataRoot" in setup and "DataRoot" in start
    assert "FullClean" in uninstall


def test_inno_installer_definition_and_clean_acceptance_script_exist():
    iss = (INSTALLER / "ContentOpsClient.iss").read_text(encoding="utf-8")
    acceptance = INSTALLER / "Test-ContentOpsClientAcceptance.ps1"
    assert acceptance.is_file()
    for text in ("autopf", "commonappdata", "yt_notifi_bootstrap.exe", "YTDOWNLOAD.exe", "Uninstall-ContentOpsClient.ps1"):
        assert text in iss
    assert 'Name: "startup"' in iss
    assert 'Name: "launchsetup"' in iss
    assert '127.0.0.1:8787/setup' in iss
    acceptance_text = acceptance.read_text(encoding="utf-8")
    for text in ("python", "node", "npm", "git", "8787", "8790", "ScheduledTask"):
        assert text in acceptance_text


def test_automatic_processing_uses_local_silence_only():
    worker = (ROOT / "app/process_worker.py").read_text(encoding="utf-8")
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    assert "127.0.0.1:8791" in config
    assert "self.bridge_valid" in worker
    assert "silence_cutter_lan_url" not in worker
    assert "SILENCE_CUTTER_LAN_URL" not in worker


def test_packaged_bootstrap_owns_local_ytdownload_startup():
    bootstrap = (INSTALLER / "yt_notifi_bootstrap.py").read_text(encoding="utf-8")
    assert "ytdownload" in bootstrap.lower()
    assert "_health_ready(8790)" in bootstrap
    assert "creationflags" in bootstrap
    setup = (INSTALLER / "Setup-ContentOpsClient.ps1").read_text(encoding="utf-8")
    assert "ContentOps Client - YTDOWNLOAD" not in setup
    assert "Register-ScheduledTask" not in setup


def test_first_install_can_show_bootstrap_permission_prompt():
    setup = (INSTALLER / "Setup-ContentOpsClient.ps1").read_text(encoding="utf-8")
    iss = (INSTALLER / "ContentOpsClient.iss").read_text(encoding="utf-8")
    bootstrap = (INSTALLER / "yt_notifi_bootstrap.py").read_text(encoding="utf-8")
    assert "Start-Process -FilePath $bootstrap" in setup
    assert "yt_notifi-bootstrap.pid" in setup
    assert 'Filename: "{app}\\yt_notifi\\yt_notifi_bootstrap.exe"' in iss
    assert 'Path(data_parent) / "ContentOps" / "Client"' in bootstrap


def test_release_package_does_not_ship_source_or_personal_env():
    build = (INSTALLER / "build-contentops-client.ps1").read_text(encoding="utf-8")
    manifest = (INSTALLER / "client-manifest.json").read_text(encoding="utf-8")
    assert "robocopy $root" not in build
    assert ".env.example" not in build
    assert "python -m uvicorn" not in manifest
    assert "yt_notifi_bootstrap.exe" in manifest
    # Build script may reference the development checkout; it never ships to targets.
    setup = (INSTALLER / "Setup-ContentOpsClient.ps1").read_text(encoding="utf-8")
    assert "D:\\YTDOWNLOAD" not in setup
    assert "app\\dashboard.html;app" in build
    assert "app;app" not in build
    assert "YTDOWNLOAD 1.0.0.exe" in build
