"""Finder entry point. The archive lives outside the application bundle."""
import errno
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import webbrowser

from pymessage import cli
from pymessage.app import make_server


def desktop():
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title('Py-Message')
    root.resizable(False, False)
    output = Path.home() / 'Library' / 'Application Support' / 'Py-Message'
    try:
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.environ.setdefault('MPLCONFIGDIR', str(output / '.state' / 'matplotlib'))
        args = cli.parser().parse_args(['app', '--output', str(output)])
        try:
            server = make_server(args)
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            args.port = 0
            server = make_server(args)
    except (OSError, ValueError, RuntimeError) as error:
        messagebox.showerror('Could not open Py-Message', str(error), parent=root)
        root.destroy()
        return 1

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = server.app.origin + '/#' + server.app.token
    panel = ttk.Frame(root, padding=24)
    panel.pack(fill='both', expand=True)
    status = tk.StringVar(value='Your local analysis workspace is open in your browser.')
    ttk.Label(panel, textvariable=status, wraplength=350).pack(anchor='w', pady=(0, 14))

    def open_workspace():
        if not webbrowser.open(url):
            status.set('Choose a default browser in System Settings, then open the workspace again.')

    def finish():
        if server.app.worker and server.app.worker.is_alive():
            root.after(250, finish)
        else:
            root.destroy()

    def quit_app():
        open_button.state(['disabled'])
        quit_button.state(['disabled'])
        status.set('Finishing any active archive operation…')
        server.shutdown()
        server.server_close()
        finish()

    open_button = ttk.Button(panel, text='Open workspace', command=open_workspace)
    open_button.pack(side='left')
    quit_button = ttk.Button(panel, text='Quit', command=quit_app)
    quit_button.pack(side='right')
    root.protocol('WM_DELETE_WINDOW', quit_app)
    root.createcommand('tk::mac::Quit', quit_app)
    root.after_idle(open_workspace)
    root.mainloop()
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(cli.main() if sys.argv[1:] else desktop())
