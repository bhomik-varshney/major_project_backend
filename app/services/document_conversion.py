"""Headless, private DOCX rendering. Text extraction does not use LibreOffice."""
from dataclasses import dataclass
from io import BytesIO
import os
from pathlib import Path
import shlex
import signal
import subprocess
from tempfile import TemporaryDirectory
import time

from pypdf import PdfReader
from pypdf.errors import PdfReadError


@dataclass
class ConversionResult:
    pdf: bytes | None
    attempts: list[dict]
    error: str | None = None


def _profile_processes(profile: Path) -> dict[int, str]:
    """Inspect without touching other profiles, user sessions, or GUI processes."""
    if os.name != 'posix':
        return {}
    marker = f'-env:UserInstallation={profile.as_uri()}'
    try:
        listing = subprocess.run(
            ['ps', '-axo', 'uid=,pid=,args='], capture_output=True,
            text=True, timeout=2, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    owned = {}
    for line in listing.stdout.splitlines():
        try:
            uid, pid, args = line.strip().split(None, 2)
            tokens = shlex.split(args)
            if (int(uid) == os.getuid() and tokens and
                    Path(tokens[0]).name in {'soffice', 'soffice.bin'} and marker in tokens):
                owned[int(pid)] = args
        except ValueError:
            continue
    return owned


def _cleanup_profile(profile: Path) -> None:
    """Only reap leftovers tied to the unique profile created by this attempt."""
    owned = _profile_processes(profile)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        current = _profile_processes(profile)
        for pid, identity in owned.items():
            if current.get(pid) == identity:
                try:
                    os.kill(pid, sig)
                except (ProcessLookupError, PermissionError):
                    pass
        if sig == signal.SIGTERM and owned:
            time.sleep(.1)


def _capture(value: bytes | str | None) -> str:
    text = value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''
    return text[:8000]


def convert_docx(content: bytes, executable: str, scratch: str | Path, *, timeout=20) -> ConversionResult:
    """Two bounded attempts; original bytes are never modified or overwritten.

    Diagnostics remain in the private processing audit, never application logs.
    Every attempt gets its own input, output and profile, preventing a stale PDF
    or a running user's LibreOffice session from satisfying the conversion.
    """
    attempts = []
    for number in (1, 2):
        with TemporaryDirectory(prefix='headless-office-', dir=scratch) as temporary:
            root = Path(temporary).resolve()
            profile = root / 'profile'
            profile.mkdir(mode=0o700)
            source = root / 'document.docx'
            source.write_bytes(content)
            source.chmod(0o600)
            target = root / 'document.pdf'
            diagnostic = dict(attempt=number, stdout='', stderr='', exit_code=None,
                              timeout_seconds=timeout, timed_out=False, generated_file_exists=False)
            process = None
            pdf = None
            # Never use killall/pkill. A new profile has no legitimate active user.
            # Stale workers are eligible only when their UID AND exact profile match.
            _cleanup_profile(profile)
            try:
                process = subprocess.Popen(
                    [executable, f'-env:UserInstallation={profile.as_uri()}',
                     '--headless', '--nologo', '--nofirststartwizard', '--norestore',
                     '--nodefault', '--nolockcheck', '--convert-to', 'pdf:writer_pdf_Export',
                     '--outdir', str(root), str(source)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env={**os.environ, 'SAL_USE_VCLPLUGIN': 'svp'},
                )
                try:
                    stdout, stderr = process.communicate(timeout=timeout)
                except subprocess.TimeoutExpired:
                    diagnostic['timed_out'] = True
                    # Reap our launcher as well as any profile-owning child worker.
                    process.terminate()
                    _cleanup_profile(profile)
                    try:
                        stdout, stderr = process.communicate(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        stdout, stderr = process.communicate(timeout=1)
                diagnostic.update(stdout=_capture(stdout), stderr=_capture(stderr),
                                  exit_code=process.returncode,
                                  generated_file_exists=target.is_file())
                if diagnostic['timed_out']:
                    diagnostic['error'] = 'conversion_timeout'
                elif process.returncode != 0:
                    diagnostic['error'] = 'converter_exit_error'
                elif not target.is_file():
                    diagnostic['error'] = 'output_missing'
                elif not 0 < target.stat().st_size <= 20 * 1024 * 1024:
                    diagnostic['error'] = 'output_size_invalid'
                else:
                    candidate = target.read_bytes()
                    if not candidate.startswith(b'%PDF-') or not len(PdfReader(BytesIO(candidate), strict=True).pages):
                        raise ValueError('Invalid converted PDF')
                    pdf = candidate
            except (OSError, subprocess.SubprocessError, ValueError, PdfReadError):
                diagnostic.setdefault('error', 'conversion_failed')
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)
                    diagnostic['exit_code'] = process.returncode
                diagnostic['generated_file_exists'] = target.is_file()
                _cleanup_profile(profile)
            attempts.append(diagnostic)
            if pdf is not None:
                return ConversionResult(pdf, attempts)
    return ConversionResult(None, attempts,
        'DOCX page conversion failed after two isolated headless attempts. '
        'The original document and directly extracted paragraphs/tables are preserved. '
        'Upload a PDF for visual review.')
