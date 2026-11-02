"""Linux child exec wrapper: a dead supervisor must not leave its worker alive."""
import ctypes
import os
import signal
import sys


def main():
    parent_pid = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Could not install parent-death signal')
    if os.getppid() != parent_pid:
        raise SystemExit(1)
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ)


if __name__ == '__main__':
    main()
