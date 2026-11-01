import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend import main, preferences_backup as backup
from backend.preferences_store import PreferencesStore, PreferencesError
from backend.video_processor import VideoProcessor

@pytest.fixture
def app_data(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'WORKSPACE_DIR', tmp_path)
    monkeypatch.setattr(main, 'tasks', {})
    monkeypatch.setattr(main, 'running_jobs', {})
    monkeypatch.setattr(main, 'video_processor', VideoProcessor(tmp_path))
    return tmp_path, TestClient(main.app)

def task(root, name, created='2026-10-19T08:00:00+00:00', status='completed', title='课程', output='note', legacy=False):
    directory = root / name if legacy else root / 'tasks' / name
    directory.mkdir(parents=True)
    manifest = {'task_id': name, 'created_at':created, 'title':title, 'output':output,
        'runtime':{'status':status, 'step':7, 'progress':100, 'elapsed_seconds':30,
            'result':{'title':title}}}
    (directory/'task.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    if status=='completed':
        (directory/'notes.md').write_text('# private note body',encoding='utf-8')
    return directory

def test_history_queries_all_tasks_beyond_startup_limit_without_reading_bodies(app_data, monkeypatch):
    root, client=app_data
    for i in range(125): task(root,f'task-{i:03}',title=f'课程 {i}')
    task(root,'legacy',title='Legacy COURSE',legacy=True)
    original=Path.read_text
    def guarded(path,*args,**kwargs):
        assert path.name!='notes.md','History must not load note bodies'
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded)
    ids=[]
    for offset in range(0,126,20):
        response=client.get('/api/tasks',params={'offset':offset,'limit':20})
        assert response.status_code==200
        data=response.json()
        assert data['total']==126
        assert data['has_more']==(offset+len(data['tasks'])<126)
        assert 'private note body' not in response.text
        ids += [row['task_id'] for row in data['tasks']]
    assert len(ids)==len(set(ids))==126
    assert main.tasks=={},'Browsing history must not hydrate every note'
    assert client.get('/api/tasks',params={'q':'legacy course'}).json()['tasks'][0]['task_id']=='legacy'

def test_filters_timezone_memory_precedence_and_empty_results(app_data):
    root,client=app_data
    task(root,'boundary',created='2026-10-18T16:30:00Z',title='操作课程')
    task(root,'failed',status='failed',title='错误课程',output='transcript')
    task(root,'active',status='processing',title='进行课程')
    active=main.new_task('processing','active');active.update(result={'title':'正在识别'},created_at='2026-10-19T10:00:00Z')
    main.tasks['active']=active
    assert [x['task_id'] for x in client.get('/api/tasks',params={'status':'active'}).json()['tasks']]==['active']
    assert client.get('/api/tasks',params={'q':'正在识别'}).json()['tasks'][0]['status']=='processing'
    assert client.get('/api/tasks',params={'status':'failed','output':'transcript'}).json()['total']==1
    query={'q':'操作','start_date':'2026-10-19','end_date':'2026-10-19','utc_offset':480}
    assert client.get('/api/tasks',params=query).json()['total']==1
    query['utc_offset']=0
    assert client.get('/api/tasks',params=query).json()['total']==0
    assert client.get('/api/tasks',params={'q':'no match'}).json()['tasks']==[]

@pytest.mark.parametrize('query',[{'limit':101},{'offset':-1},{'status':'nonsense'},
    {'start_date':'2026-10-20','end_date':'2026-10-19'},{'start_date':'bad'},{'utc_offset':9999}])
def test_invalid_history_filters_are_rejected(app_data,query):
    assert app_data[1].get('/api/tasks',params=query).status_code==400

def test_older_task_can_open_download_and_delete_after_limited_startup(app_data, monkeypatch):
    root,client=app_data
    directory=task(root,'old-task',title='旧笔记',legacy=True)
    monkeypatch.setattr(main,'TASK_HISTORY_LIMIT',1)
    assert client.get('/api/tasks',params={'q':'旧笔记'}).json()['total']==1
    assert not main.tasks
    assert client.get('/api/task/old-task').json()['result']['markdown']=='# private note body'
    main.tasks.clear()
    assert client.get('/api/download/old-task').status_code==200
    main.tasks.clear()
    assert client.delete('/api/task/old-task').status_code==200
    assert not directory.exists()

def test_corrupt_history_does_not_hide_valid_rows(app_data):
    root,client=app_data
    task(root,'good')
    directory=root/'tasks'/'bad';directory.mkdir()
    (directory/'task.json').write_text('{broken',encoding='utf-8')
    assert client.get('/api/tasks').json()['total']==1

def manual_backup(root):
    store=PreferencesStore(root)
    first=store.save({'schema':3,'ui':{'theme':'dark'},'templates':[{'id':'t','name':'课程','settings':{}}],
        'customs':[{'id':'c','label':'模型'}]},None)
    document=backup.export_settings(root)
    latest=store.save({'schema':3,'ui':{'theme':'light'},'templates':[],'customs':[]},first['revision'])
    return store,latest,document

def test_manual_export_and_import_exclude_credentials_without_automatic_backups(app_data):
    root,client=app_data
    store,current,document=manual_backup(root)
    witnesses={'.profile_entropy.bin':b'salt','llm_keys.json':b'saved-secret',
        'bili_credentials.json':b'login-secret','_model_cache/model.bin':b'model','tasks/existing/note.md':b'note'}
    for name,value in witnesses.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
    exported=client.get('/api/preferences/export')
    assert exported.status_code==200
    assert exported.json()['format']=='ai-video-note-settings'
    assert 'saved-secret' not in exported.text and 'login-secret' not in exported.text
    assert client.get('/api/preferences/backups').status_code==404
    before=store.path.read_bytes()
    request={'document':document}
    preview=client.post('/api/preferences/restore/preview',json=request).json()
    assert preview['templates']==1 and preview['profiles']==1 and preview['created_at']
    assert store.path.read_bytes()==before,'Preview must not restore anything'
    restored=client.post('/api/preferences/restore',json={**request,
        'backup_revision':preview['backup_revision'],'current_revision':preview['current_revision']})
    assert restored.status_code==200
    assert restored.json()['preferences']['ui']['theme']=='dark'
    assert not (root/'settings_backups').exists()
    for name,value in witnesses.items():assert (root/name).read_bytes()==value

@pytest.mark.parametrize('change',['current','backup'])
def test_restore_refuses_changed_current_or_source(app_data,change):
    root,client=app_data
    store,current,document=manual_backup(root)
    source={'document':document}
    preview=client.post('/api/preferences/restore/preview',json=source).json()
    if change=='current':store.save({'schema':3,'ui':{'theme':'system'}},current['revision'])
    else:document['preferences']['ui']['theme']='system'
    before=store.path.read_bytes()
    response=client.post('/api/preferences/restore',json={**source,'backup_revision':preview['backup_revision'],'current_revision':preview['current_revision']})
    assert response.status_code==409
    assert store.path.read_bytes()==before

def test_restore_exported_document_and_recover_corrupt_current(app_data):
    root,client=app_data
    store,_,document=manual_backup(root)
    document=client.get('/api/preferences/export').json()
    document['preferences']['ui']['theme']='system'
    document['preferences']['api_key']='must be removed'
    store.path.write_bytes(b'{damaged-current')
    preview=client.post('/api/preferences/restore/preview',json={'document':document}).json()
    response=client.post('/api/preferences/restore',json={'document':document,
        'backup_revision':preview['backup_revision'],'current_revision':preview['current_revision']})
    assert response.status_code==200
    assert response.json()['preferences']['ui']['theme']=='system'
    assert 'must be removed' not in store.path.read_text(encoding='utf-8')
    assert not (root/'settings_backups').exists()

@pytest.mark.parametrize('source',[{'backup_id':'../llm_keys.json'}, {'backup_id':'llm_keys.json'},
    {'document':{'schema':999,'ui':{}}},{'document':{'version':1,'entries':[]}},
    {'document':{},'backup_id':'preferences.previous.json'}])
def test_invalid_restore_source_keeps_settings(app_data,source):
    root,client=app_data
    store,_,document=manual_backup(root);before=store.path.read_bytes()
    assert client.post('/api/preferences/restore/preview',json=source).status_code in {409,422}
    assert store.path.read_bytes()==before

def test_restore_failed_replace_keeps_current_without_automatic_backups(app_data,monkeypatch):
    root,client=app_data
    store,_,document=manual_backup(root);before=store.path.read_bytes()
    source={'document':document}
    preview=client.post('/api/preferences/restore/preview',json=source).json()
    original=Path.replace
    def broken(path,target):
        if Path(target)==store.path:raise OSError('simulated busy file')
        return original(path,target)
    monkeypatch.setattr(Path,'replace',broken)
    response=client.post('/api/preferences/restore',json={**source,
        'backup_revision':preview['backup_revision'],'current_revision':preview['current_revision']})
    assert response.status_code==503
    assert store.path.read_bytes()==before
    assert not list(root.rglob('*.tmp'))
    assert not (root/'settings_backups').exists()


def test_existing_local_backup_files_are_untouched(app_data):
    root,client=app_data
    directory=root/'settings_backups';directory.mkdir()
    old=directory/'preferences.previous.json';old.write_bytes(b'user existing backup')
    store,current,document=manual_backup(root)
    preview=client.post('/api/preferences/restore/preview',json={'document':document}).json()
    response=client.post('/api/preferences/restore',json={'document':document,
        'backup_revision':preview['backup_revision'],'current_revision':preview['current_revision']})
    assert response.status_code==200
    assert list(directory.iterdir())==[old]
    assert old.read_bytes()==b'user existing backup'
