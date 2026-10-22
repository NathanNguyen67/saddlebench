#!/usr/bin/env python3
"""Best-effort X11 image opener; GTK/ImageMagick or built-in Tk fallback.

No network or browser required. Does not alter image or report contents.
"""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import sys


def _tk_window(path):
    import tkinter as tk
    root=tk.Tk()
    root.title('SaddleBench · '+path.name)
    frame=tk.Frame(root)
    frame.pack(fill='both',expand=True)
    photo=tk.PhotoImage(file=str(path))
    width,height=photo.width(),photo.height()
    canvas=tk.Canvas(frame,width=min(width,1250),height=min(height,850),highlightthickness=0)
    xv=tk.Scrollbar(frame,orient='horizontal',command=canvas.xview)
    yv=tk.Scrollbar(frame,orient='vertical',command=canvas.yview)
    canvas.configure(xscrollcommand=xv.set,yscrollcommand=yv.set,scrollregion=(0,0,width,height))
    canvas.grid(row=0,column=0,sticky='nsew')
    yv.grid(row=0,column=1,sticky='ns')
    xv.grid(row=1,column=0,sticky='ew')
    frame.grid_columnconfigure(0,weight=1)
    frame.grid_rowconfigure(0,weight=1)
    canvas.create_image(0,0,anchor='nw',image=photo)
    canvas.image=photo
    root.mainloop()


def open_image(path,*,env=None):
    env=os.environ if env is None else env
    img=Path(path).expanduser().absolute()
    if not img.is_file():
        raise FileNotFoundError(str(img))
    if str(env.get('SADDLEBENCH_OPEN_IMAGE','1')).lower() in ('0','false','off','no'):
        return 'SKIPPED_DISABLED'
    if not (env.get('DISPLAY') or env.get('WAYLAND_DISPLAY')):
        return 'SKIPPED_NO_GRAPHICAL_DISPLAY'
    # Prefer lightweight image viewers, not a web browser.
    apps = (('eog',),('ristretto',),('feh',),('display',),('gwenview',))
    for app in apps:
        if img.suffix.lower()=='.svg' and app[0] in ('feh','display'):
            continue
        exe=shutil.which(app[0])
        if not exe:continue
        try:
            proc=subprocess.Popen([exe, str(img)],stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                  start_new_session=True,env=dict(env))
            try:
                exit_code=proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                return 'REQUESTED:'+app[0]
            if exit_code==0:
                return 'REQUESTED:'+app[0]
        except (OSError,ValueError):
            continue
    # ASE's X11 GUI commonly uses Tk. Tk 8.6 supports PNG without Pillow.
    if img.suffix.lower()=='.png':
        try:
            import tkinter
            proc=subprocess.Popen([sys.executable, __file__, '--tk-view', str(img)],
                                  stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,start_new_session=True,env=dict(env))
            try:
                rc=proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                return 'REQUESTED:tkinter'
            if rc==0:
                return 'REQUESTED:tkinter'
        except (OSError, ImportError):
            pass
    # A generic desktop handler is less reliable under bare SSH than a GUI app.
    # Try it only when the Tk image-viewer fallback was not usable.
    if shutil.which('xdg-open'):
        try:
            proc=subprocess.Popen(['xdg-open',str(img)],stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                  start_new_session=True,env=dict(env))
            try: status=proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired: return 'REQUESTED:xdg-open'
            if status==0: return 'REQUESTED:xdg-open'
        except (OSError,ValueError): pass
    return 'FAILED_NO_IMAGE_VIEWER'


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument('--tk-view', action='store_true')
    p.add_argument('image')
    a=p.parse_args(argv)
    if a.tk_view:
        _tk_window(Path(a.image))
        return 0
    print('IMAGE_OPEN='+open_image(a.image))
    return 0

if __name__=='__main__':
    sys.exit(main())
