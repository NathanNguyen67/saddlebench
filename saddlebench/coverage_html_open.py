"""Best-effort X11/desktop HTML opener; never changes report or scoring status.

A forwarded X11 display lets remote GUI applications show windows locally.
The remote machine still needs an HTML-capable graphical application.
"""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def open_html(path, *, env=None):
    """Ask a graphical application to open a finished report, without blocking.

    Return status based on process-launch observations, not proof of a visible
    window. No browser is installed or modified by this helper.
    """
    env = os.environ if env is None else env
    report = Path(path).expanduser().absolute()
    if not report.is_file():
        raise FileNotFoundError(str(report))
    if str(env.get('SADDLEBENCH_OPEN_HTML', '1')).lower() in ('0', 'false', 'off', 'no'):
        return 'SKIPPED_DISABLED'
    if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
        return 'SKIPPED_NO_GRAPHICAL_DISPLAY'

    url = report.as_uri()
    candidates = []
    override = env.get('SADDLEBENCH_HTML_BROWSER')
    if override:
        argv = shlex.split(override)
        if argv:
            candidates.append(argv)
    # Graphical browsers before generic handlers to avoid terminal text viewers.
    candidates.extend([[name] for name in (
        'firefox', 'chromium', 'chromium-browser', 'google-chrome',
        'google-chrome-stable', 'brave-browser')])
    candidates.extend((['xdg-open'], ['gio', 'open']))
    attempted = False
    for argv in candidates:
        exe = shutil.which(argv[0])
        if not exe:
            continue
        attempted = True
        try:
            process = subprocess.Popen([exe, *argv[1:], url],
                                       stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL,
                                       start_new_session=True, env=dict(env))
            try:
                status = process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                # A GUI browser normally stays alive while its window is open.
                return 'REQUESTED:' + argv[0]
            if status == 0:
                return 'REQUESTED:' + argv[0]
        except (OSError, ValueError):
            continue
    return 'FAILED_NO_WORKING_OPENER' if attempted else 'SKIPPED_NO_BROWSER'


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Open an existing HTML report using the remote X11 display')
    parser.add_argument('html', help='Existing HTML file')
    args = parser.parse_args(argv)
    status = open_html(args.html)
    print('HTML_OPEN=' + status)
    if status.startswith(('SKIPPED_', 'FAILED_')):
        print('HTML_OPEN_HINT=An HTML browser must be installed on Saskatchewan and DISPLAY must be forwarded; otherwise use open_in_windows.ps1 on Windows.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
