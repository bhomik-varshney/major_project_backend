"""Reusable trusted-source, one-attempt LaTeX compiler with private diagnostics."""
from pathlib import Path
import os,re,shutil,signal,subprocess,tempfile
from app.services.cover_letter_render import engine_path,RenderFailure

def compile_once(source,assets,destination,settings,index,check=lambda:None):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True,mode=0o700)
    (destination/'document.tex').write_text(source)
    for name,path in assets.items():shutil.copyfile(path,destination/name)
    binary=engine_path(settings)
    if not binary:raise RenderFailure('latex_engine_unavailable',{'attempts':[],'source_retained':True})
    check()
    with tempfile.TemporaryDirectory(prefix='resume-build-',dir=destination) as scratch:
        directory=Path(scratch);(directory/'document.tex').write_text(source)
        for name,path in assets.items():shutil.copyfile(path,directory/name)
        environment={k:v for k,v in os.environ.items() if k in {'PATH','HOME','TMPDIR','SYSTEMROOT','LANG'}}
        environment.update(openin_any='p',openout_any='p',shell_escape='f',TEXMFOUTPUT=scratch,TEXMFVAR=str(directory/'texmf-var'),TEXMFCONFIG=str(directory/'texmf-config'))
        command=[binary,'-no-shell-escape','-interaction=nonstopmode','-halt-on-error','-file-line-error','document.tex']
        process=subprocess.Popen(command,cwd=directory,env=environment,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
        timeout=False
        try:
            # Observe cancellation between short waits; terminate only this owned group.
            import time
            end=time.monotonic()+settings.cover_letter_compile_seconds
            while True:
                try:stdout,stderr=process.communicate(timeout=min(.25,max(.01,end-time.monotonic())));break
                except subprocess.TimeoutExpired:
                    check()
                    if time.monotonic()>=end:
                        timeout=True;os.killpg(process.pid,signal.SIGKILL);stdout,stderr=process.communicate();break
        except BaseException:
            if process.poll() is None:os.killpg(process.pid,signal.SIGKILL)
            process.communicate();raise
        log=(directory/'document.log').read_text(errors='replace') if (directory/'document.log').is_file() else ''
        for kind,data in [('stdout',stdout.decode(errors='replace')),('stderr',stderr.decode(errors='replace')),('log',log)]:
            (destination/f'attempt-{index}.{kind}').write_text(data)
        exists=(directory/'document.pdf').is_file()
        summary={'attempt':index,'exit_code':process.returncode,'timeout':timeout,'generated_file_exists':exists,
            'stdout_bytes':len(stdout),'stderr_bytes':len(stderr),'log_reference':f'attempt-{index}.log',
            'command':[Path(binary).name,*command[1:]],'diagnostics':[line[:220] for line in (stdout.decode(errors='replace')+'\n'+log).splitlines() if re.match(r'^! (LaTeX|Package|Font)|^Missing character:|^Overfull \\[hv]box',line)][:8]}
        if exists:shutil.copyfile(directory/'document.pdf',destination/'document.pdf')
        check()
        return summary,log
