#!/usr/bin/env python3
"""Run a command with a wall-clock limit; terminate its process group on exit."""
import os
import math
import signal
import subprocess
import sys


def run(seconds, command, **options):
    if not math.isfinite(seconds) or not 0 < seconds <= 86400 or not command:
        raise ValueError('finite timeout in (0, 86400] and command required')
    child = subprocess.Popen(command, start_new_session=True, **options)

    def stop(signum, frame):
        raise InterruptedError(signum)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, stop)
    try:
        return child.wait(timeout=seconds)
    except subprocess.TimeoutExpired:
        return 124
    except InterruptedError as exc:
        return 128 + exc.args[0]
    finally:
        # Descendants can outlive a successful parent too. Never leave review work
        # running after the result has been returned or a fallback has started.
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


if __name__ == '__main__':
    try:
        raise SystemExit(run(float(sys.argv[1]), sys.argv[2:]))
    except (ValueError, IndexError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
