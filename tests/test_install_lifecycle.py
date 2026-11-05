import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import pytest
import launcher
from backend import install_lifecycle, main

ROOT = Path(launcher.__file__).parent
HELPER = ROOT / 'scripts/uninstall_helper.ps1'
WINDOWS = pytest.mark.skipif(sys.platform != 'win32', reason='Windows uninstaller helpers')


def test_version_and_release_gate_agree():
    assert launcher.VERSION == main.app.version == '1.0.1'
    assert 'v1.0.1</span>' in (ROOT/'frontend/index.html').read_text(encoding='utf-8')
    workflow = (ROOT/'.github/workflows/release.yml').read_text(encoding='utf-8')
    assert workflow.index('verify_installer.ps1') < workflow.index('- name: Upload Windows asset')
    source = (ROOT/'scripts/installer.iss').read_text(encoding='utf-8')
    assert "RunUninstallHelper('Prepare')" in source and "RunUninstallHelper('Verify')" in source
    assert source.index("RunUninstallHelper('Prepare')") > source.index('CurUninstallStep = usUninstall')
    assert 'ExitUninstallProcess(2)' in source
    assert 'filesandordirs' not in source
    assert 'Type: dirifempty; Name: "{app}"' in source
    assert (ROOT/'scripts/install-owner.json').is_file()


def test_shutdown_signals_are_scoped_to_install_directory():
    name = install_lifecycle.shutdown_event_name
    assert name('C:\\Apps\\One\\') == name('c:\\apps\\one')
    assert name('C:\\Apps\\One') != name('D:\\AI-Video-Note')


def powershell(code, *args):
    executable = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    command = ". $args[0] -LibraryOnly; " + code
    # Use a wrapper file instead of interpolating filesystem paths into shell code.
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        wrapper = Path(directory)/'check.ps1'
        wrapper.write_text("$ErrorActionPreference='Stop'\n" + command, encoding='utf-8-sig')
        result = subprocess.run([str(executable),'-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(wrapper),str(HELPER),*map(str,args)],
                                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


@WINDOWS
def test_windows_powershell_5_parses_both_scripts():
    powershell("foreach($path in $args[1..2]){$tokens=$null;$errors=$null;[void][System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors);if($errors.Count){throw ($errors|Out-String)}}", HELPER, ROOT/'scripts/verify_installer.ps1')


@WINDOWS
def test_powershell_signal_shuts_down_only_selected_listener(tmp_path):
    one, two = threading.Event(), threading.Event()
    first = install_lifecycle.listen_for_shutdown(tmp_path/'安装 one', one.set)
    second = install_lifecycle.listen_for_shutdown(tmp_path/'other', two.set)
    try:
        powershell('Request-InstallationShutdown ([IO.Path]::GetFullPath($args[1]));', tmp_path/'安装 one')
        assert one.wait(2)
        assert not two.wait(.1)
    finally:
        first.close(); second.close()
    assert not first.thread.is_alive() and not second.thread.is_alive()


@WINDOWS
def test_process_tree_excludes_other_installations_reused_parents_and_uninstaller():
    powershell(r'''
function Record($id,$parent,$name,$path,$second){[pscustomobject]@{ProcessId=$id;ParentProcessId=$parent;Name=$name;ExecutablePath=$path;CreationDate=([datetime]'2026-11-05').AddSeconds($second)}}
$exe='C:\Apps\One\AI-Video-Note.exe'
$records=@((Record 10 1 'AI-Video-Note.exe' $exe 10),(Record 11 10 'AI-Video-Note.exe' $exe 11),
 (Record 12 11 'codex.exe' 'C:\Tools\codex.exe' 12),(Record 20 1 'AI-Video-Note.exe' 'D:\Other\AI-Video-Note.exe' 10),
 (Record 13 11 'unins000.exe' 'C:\Apps\One\unins000.exe' 13),(Record 14 13 'powershell.exe' 'powershell.exe' 14),
 (Record 15 10 'unrelated.exe' 'old-process.exe' 1))
$ids=@(Get-InstallationProcessTree $records $exe | Select-Object -ExpandProperty ProcessId | Sort-Object)
if(($ids -join ',') -ne '10,11,12'){throw ('Wrong process scope: '+($ids -join ','))}
function Get-CimInstance { return (Record 10 1 'AI-Video-Note.exe' $exe 99) }
if(Get-LiveTrackedProcess $records[0]){throw 'Reused PID was accepted'}
''')


@WINDOWS
def test_force_stop_exits_only_the_target_installation(tmp_path):
    # Harmless loopback pings stand in for two independently installed executables.
    one, two = tmp_path/'one', tmp_path/'two'
    one.mkdir(); two.mkdir()
    source = Path(os.environ['SystemRoot'])/'System32/ping.exe'
    for directory in (one,two): shutil.copy2(source,directory/'AI-Video-Note.exe')
    processes=[]
    try:
        for directory in (one,two):
            processes.append(subprocess.Popen([str(directory/'AI-Video-Note.exe'),'-t','127.0.0.1'],
                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW))
        powershell('Stop-InstallationProcesses $args[1];',one)
        assert processes[0].wait(timeout=2) is not None
        assert processes[1].poll() is None, 'Another installation with the same executable name was stopped'
    finally:
        for process in processes:
            if process.poll() is None: process.terminate()
            process.wait(timeout=5)


@WINDOWS
def test_owned_workspace_removed_but_external_data_and_program_files_stay(tmp_path):
    install = tmp_path/'installed app'; install.mkdir()
    shutil.copy2(ROOT/'scripts/install-owner.json', install/'install-owner.json')
    (install/'AI-Video-Note.exe').write_bytes(b'program fixture')
    workspace = install/'workspace'; workspace.mkdir()
    for name in ('preferences.json','bili_credentials.json','note.md','model.bin'):
        (workspace/name).write_text('fictional data',encoding='utf-8')
    (install/'.env').write_text('fictional config',encoding='utf-8')
    custom = install/'custom-models'; custom.mkdir()
    (custom/'model.bin').write_bytes(b'fictional model')
    (install/'custom-note.md').write_text('fictional note',encoding='utf-8')
    outside = tmp_path/'external vault'; outside.mkdir()
    sentinel = outside/'keep.md'; sentinel.write_text('external note',encoding='utf-8')
    powershell('Remove-InstallationData $args[1];', install)
    assert not workspace.exists() and not (install/'.env').exists()
    assert not custom.exists() and not (install/'custom-note.md').exists()
    assert (install/'AI-Video-Note.exe').read_bytes() == b'program fixture'
    assert sentinel.read_text(encoding='utf-8') == 'external note'


@WINDOWS
def test_unowned_directory_and_junction_are_rejected_without_deleting_data(tmp_path):
    install = tmp_path/'unowned'; install.mkdir()
    workspace = install/'workspace'; workspace.mkdir()
    sentinel = workspace/'keep'; sentinel.write_text('keep',encoding='utf-8')
    powershell("$rejected=$false;try{Remove-InstallationData $args[1]}catch{$rejected=$true};if(-not $rejected){throw 'Unowned directory accepted'}",install)
    assert sentinel.exists()
    shutil.copy2(ROOT/'scripts/install-owner.json',install/'install-owner.json')
    outside=tmp_path/'outside';outside.mkdir();(outside/'keep').write_text('keep',encoding='utf-8')
    powershell(r'''
$link=Join-Path $args[1] 'workspace\external'
New-Item -ItemType Junction -Path $link -Target $args[2] | Out-Null
try{$rejected=$false;try{Remove-InstallationData $args[1]}catch{$rejected=$true};if(-not $rejected){throw 'Junction accepted'}}finally{[IO.Directory]::Delete($link)}
''',install,outside)
    assert sentinel.exists() and (outside/'keep').exists()


@WINDOWS
def test_verification_reports_residual_program_instead_of_success(tmp_path):
    install=tmp_path/'residual';install.mkdir()
    (install/'AI-Video-Note.exe').write_bytes(b'locked-program-fixture')
    powershell(r'''
function Get-ProcessSnapshot {return @()}
$failed=$false;try{Assert-Uninstalled $args[1]}catch{$failed=$true};if(-not $failed){throw 'Residual executable was reported as clean'}
''',install)


@pytest.mark.asyncio
async def test_managed_shutdown_cancels_agent_workers(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from backend import agent_auto
    cancelled = asyncio.Event()
    async def worker():
        try: await asyncio.Future()
        finally: cancelled.set()
    job = asyncio.create_task(worker()); await asyncio.sleep(0)
    monkeypatch.setattr(agent_auto,'workers',{'test':job})
    monkeypatch.setattr(main,'tasks',{'test':{'_cancel_event':threading.Event()}})
    monkeypatch.setattr(main,'running_jobs',{})
    monkeypatch.setattr(main,'_active_transcriptions',set())
    monkeypatch.setattr(main,'model_downloads',SimpleNamespace(pause=lambda:None,thread=None))
    async def stopped(): pass
    monkeypatch.setattr(main.asr_process,'stop_workers',stopped)
    callbacks=[]
    await main.shutdown_application(lambda:callbacks.append(True))
    assert cancelled.is_set() and callbacks == [True]
    assert main.tasks['test']['_cancel_requested']
