from pathlib import Path
import importlib.util
from types import SimpleNamespace


def test_build_metadata_excludes_caches_and_install_provenance(tmp_path, monkeypatch):
    script=Path(__file__).resolve().parents[1]/'scripts/stage_metadata.py'
    spec=importlib.util.spec_from_file_location('stage_metadata',script)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source=tmp_path/'example-1.0.dist-info'; source.mkdir()
    (source/'METADATA').write_text('Name: example\nVersion: 1.0\n')
    (source/'direct_url.json').write_text('{"url":"file:///private-build-folder"}')
    cache=source/'licenses/__pycache__'; cache.mkdir(parents=True)
    (cache/'authors.pyc').write_bytes(b'private build cache')
    (source/'licenses/LICENSE').write_text('Public license')
    monkeypatch.setattr(module.importlib.metadata,'distribution',lambda name: SimpleNamespace(_path=source))
    result=module.stage_metadata('example',tmp_path/'staged')
    staged=Path(result['path'])
    assert result['name']=='example-1.0.dist-info'
    assert (staged/'METADATA').read_text()==(source/'METADATA').read_text()
    assert (staged/'licenses/LICENSE').exists()
    assert not (staged/'direct_url.json').exists()
    assert not list(staged.rglob('*.pyc'))
    assert not list(staged.rglob('__pycache__'))
