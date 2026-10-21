"""Own the adapter tree even if the requesting workflow is cancelled or dies."""
import os
import signal
import subprocess
import sys


def main():
    command=sys.argv[1:]
    if command[:1]==['--']:command=command[1:]
    parent=os.getppid()
    child=subprocess.Popen(command)
    try:
        while True:
            try:return child.wait(timeout=.2)
            except subprocess.TimeoutExpired:
                if os.getppid()!=parent:return 1
    finally:
        if child.poll() is None:
            # MCP SDK starts this launcher in its own session on POSIX.
            # Only its own adapter group may be signalled, never the host group.
            if os.name=='posix' and os.getpgrp()==os.getpid():os.killpg(os.getpid(),signal.SIGTERM)
            else:
                child.terminate()
                try:child.wait(timeout=1)
                except subprocess.TimeoutExpired:child.kill();child.wait()

if __name__=='__main__':sys.exit(main())
