from backend import first_run


def test_first_launch_then_completed_launch(monkeypatch, tmp_path):
    shown = []
    opened = []
    monkeypatch.setattr(first_run, 'show_welcome', lambda *args: shown.append(args))
    first_run.open_interface('http://localhost', tmp_path, opened.append)
    assert len(shown) == 1
    assert not opened
    first_run.mark_completed(tmp_path)
    first_run.open_interface('http://localhost', tmp_path, opened.append)
    assert opened == ['http://localhost']
    assert len(shown) == 1


def test_quiet_start_never_opens_welcome_or_browser(monkeypatch, tmp_path):
    def unexpected(*args):
        raise AssertionError('Quiet startup opened a window')
    monkeypatch.setattr(first_run, 'show_welcome', unexpected)
    first_run.open_interface('http://localhost', tmp_path, unexpected, quiet=True)
    assert not first_run.welcome_completed(tmp_path)


def test_invalid_state_still_shows_welcome(tmp_path):
    (tmp_path / '_welcome.json').write_text('invalid', encoding='utf-8')
    assert not first_run.welcome_completed(tmp_path)
