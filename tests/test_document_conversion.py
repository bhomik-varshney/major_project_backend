"""Synthetic conversion failures, process isolation, and direct-text fallback."""
from io import BytesIO
import os
from pathlib import Path
import signal
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from pypdf import PdfWriter
import pytest

from app.core.config import Settings
from app.services import document_conversion as conversion
from app.services import document_intelligence as intelligence


def pdf_bytes():
    stream = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.write(stream)
    return stream.getvalue()


def fake_process(monkeypatch, actions):
    calls = []
    def start(args, **kwargs):
        calls.append((args, kwargs))
        action = actions[len(calls) - 1]
        if isinstance(action, Exception):
            raise action
        target = Path(args[args.index('--outdir') + 1]) / 'document.pdf'
        if action.get('pdf') is not None:
            target.write_bytes(action['pdf'])
        process = Mock()
        process.returncode = action.get('exit_code', 0)
        process.poll.return_value = process.returncode
        process.communicate.side_effect = action.get('communicate', [(b'converted', b'converter detail')])
        return process
    monkeypatch.setattr(conversion.subprocess, 'Popen', start)
    monkeypatch.setattr(conversion, '_cleanup_profile', lambda profile: None)
    return calls


def test_retry_uses_fresh_headless_profiles_and_preserves_original(tmp_path, monkeypatch):
    original = tmp_path / 'original.docx'
    original.write_bytes(b'original private bytes')
    calls = fake_process(monkeypatch, [dict(exit_code=-6), dict(pdf=pdf_bytes())])
    result = conversion.convert_docx(original.read_bytes(), '/fake/soffice', tmp_path)
    assert result.pdf == pdf_bytes() and result.error is None
    assert original.read_bytes() == b'original private bytes'
    assert len(result.attempts) == len(calls) == 2
    assert result.attempts[0]['exit_code'] == -6
    assert result.attempts[0]['stderr'] == 'converter detail'
    assert result.attempts[1]['generated_file_exists']
    profiles = []
    for args, kwargs in calls:
        assert {'--headless', '--nologo', '--nofirststartwizard', '--nodefault', '--nolockcheck'} <= set(args)
        profiles.append(next(arg for arg in args if arg.startswith('-env:UserInstallation=')))
        assert kwargs['stderr'] == subprocess.PIPE and kwargs['stdout'] == subprocess.PIPE
        assert kwargs['env']['SAL_USE_VCLPLUGIN'] == 'svp'
        assert 'shell' not in kwargs
    assert profiles[0] != profiles[1]
    assert list(tmp_path.iterdir()) == [original]


@pytest.mark.parametrize('action,error', [
    (dict(exit_code=1), 'converter_exit_error'),
    (dict(exit_code=0), 'output_missing'),
    (dict(pdf=b''), 'output_size_invalid'),
    (dict(pdf=b'%PDF-invalid'), 'conversion_failed'),
    (OSError('cannot launch'), 'conversion_failed'),
])
def test_failed_conversion_is_bounded_and_explicit(tmp_path, monkeypatch, action, error):
    calls = fake_process(monkeypatch, [action, action])
    result = conversion.convert_docx(b'input', '/fake/soffice', tmp_path)
    assert result.pdf is None and 'two isolated headless attempts' in result.error
    assert len(calls) == len(result.attempts) == 2
    assert all(attempt['error'] == error for attempt in result.attempts)
    assert not list(tmp_path.iterdir())


def test_timeout_captures_outputs_and_retries_once(tmp_path, monkeypatch):
    timeout = subprocess.TimeoutExpired('soffice', 1)
    calls = fake_process(monkeypatch, [
        dict(exit_code=-15, communicate=[timeout, (b'partial stdout', b'partial stderr')]),
        dict(pdf=pdf_bytes()),
    ])
    result = conversion.convert_docx(b'input', '/fake/soffice', tmp_path, timeout=1)
    attempt = result.attempts[0]
    assert attempt['timed_out'] and attempt['timeout_seconds'] == 1
    assert attempt['stdout'] == 'partial stdout' and attempt['stderr'] == 'partial stderr'
    assert attempt['error'] == 'conversion_timeout' and result.pdf is not None
    assert len(calls) == 2


def test_stale_detection_excludes_other_users_profiles_and_programs(tmp_path, monkeypatch):
    profile = tmp_path / 'private profile'
    marker = f'-env:UserInstallation={profile.as_uri()}'
    uid = os.getuid()
    rows = [
        f'{uid} 101 /opt/libreoffice/soffice.bin --headless {marker}',
        f'{uid} 102 /opt/libreoffice/soffice.bin --headless {marker}-other',
        f'{uid + 1} 103 /opt/libreoffice/soffice.bin --headless {marker}',
        f'{uid} 104 /usr/bin/python {marker}',
        f'{uid} 105 /Applications/LibreOffice.app/Contents/MacOS/soffice',
    ]
    monkeypatch.setattr(conversion.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='\n'.join(rows)))
    assert list(conversion._profile_processes(profile)) == [101]


def test_cleanup_does_not_signal_reused_pid_or_other_process(tmp_path, monkeypatch):
    snapshots = iter([{101: 'owned profile'}, {101: 'unrelated process'}, {101: 'unrelated process'}])
    monkeypatch.setattr(conversion, '_profile_processes', lambda path: next(snapshots))
    monkeypatch.setattr(conversion.time, 'sleep', lambda seconds: None)
    kill = Mock()
    monkeypatch.setattr(conversion.os, 'kill', kill)
    conversion._cleanup_profile(tmp_path)
    kill.assert_not_called()


def test_cleanup_only_signals_owned_stale_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(conversion, '_profile_processes', lambda path: {101: 'owned profile'})
    monkeypatch.setattr(conversion.time, 'sleep', lambda seconds: None)
    kill = Mock()
    monkeypatch.setattr(conversion.os, 'kill', kill)
    conversion._cleanup_profile(tmp_path)
    assert [call.args for call in kill.call_args_list] == [(101, signal.SIGTERM), (101, signal.SIGKILL)]


def test_docx_keeps_direct_text_and_diagnostics_when_rendering_fails(tmp_path, monkeypatch):
    content = (Path(__file__).parent / 'fixtures/resumes/table_education.docx').read_bytes()
    monkeypatch.setattr(intelligence, 'converter', lambda settings: '/fake/soffice')
    result = conversion.ConversionResult(None, [dict(exit_code=-6, stderr='failure')], 'Headless conversion failed.')
    monkeypatch.setattr(intelligence, 'convert_docx', lambda *args: result)
    settings = Settings(_env_file=None, document_storage_path=tmp_path / 'documents')
    prepared = intelligence.prepare_document(content, 'resume.docx', str(uuid4()), settings, tmp_path)
    assert prepared['exact_text'] and prepared['blocks'] and not prepared['pages']
    assert 'Headless conversion failed.' in prepared['warnings']
    assert prepared['conversion']['status'] == 'failed'
    assert prepared['conversion']['attempts'][0]['stderr'] == 'failure'


def test_docx_reading_does_not_launch_libreoffice(tmp_path, monkeypatch):
    from app.services.resume_text import extract_text
    content = (Path(__file__).parent / 'fixtures/resumes/table_education.docx').read_bytes()
    launch = Mock(side_effect=AssertionError('Text extraction must not launch LibreOffice'))
    monkeypatch.setattr(conversion.subprocess, 'Popen', launch)
    blocks, _, _ = extract_text(content, 'resume.docx', str(uuid4()),
        Settings(_env_file=None, document_storage_path=tmp_path))
    assert blocks and any(block.get('cells') for block in blocks)
    launch.assert_not_called()
