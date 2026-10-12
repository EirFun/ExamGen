"""Desktop entry point used by the Windows executable and local smoke checks."""
import argparse
import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import webbrowser


def data_directory():
    if os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    else:
        base = Path(os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share')
    return base / 'ExamGen'


def main():
    parser = argparse.ArgumentParser(description='Run the ExamGen desktop application.')
    parser.add_argument('--no-browser', action='store_true', help='Do not open a browser automatically.')
    parser.add_argument('--port', type=int, default=0, help='Local port; 0 chooses a free port automatically.')
    parser.add_argument('--data-dir', type=Path, help='Override the persistent data directory.')
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('Port must be between 0 and 65535.')
    data = args.data_dir or Path(os.environ.get('EXAMGEN_DATA_DIR') or data_directory())
    data.mkdir(parents=True, exist_ok=True)
    os.environ['EXAMGEN_DATA_DIR'] = str(data.resolve())
    # Configure persistent storage before importing the app. PyInstaller's extracted
    # bundle is temporary and must never be used to store the user's papers.
    from app import app
    import uvicorn

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == 'nt':
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        listener.bind(('127.0.0.1', args.port))
        port = listener.getsockname()[1]
        config = uvicorn.Config(app, host='127.0.0.1', port=port, loop='asyncio', http='h11', ws='none', log_level='warning')
        server = uvicorn.Server(config)
        address = f'http://127.0.0.1:{port}'
        print('\nExamGen - Medical Exam Analyzer\n', flush=True)
        print(f'Your app: {address}', flush=True)
        print(f'Your saved papers: {data.resolve()}', flush=True)
        print('Keep this window open while using ExamGen. Press Ctrl+C to stop.\n', flush=True)

        if not args.no_browser:
            def open_when_ready():
                for _ in range(300):
                    if server.started:
                        webbrowser.open(address)
                        return
                    if server.should_exit:
                        return
                    time.sleep(0.1)
            threading.Thread(target=open_when_ready, daemon=True).start()
        server.run(sockets=[listener])


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        details = traceback.format_exc()
        print('\nExamGen could not start:\n' + details, file=sys.stderr, flush=True)
        try:
            log = data_directory() / 'startup-error.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(details, encoding='utf-8')
            print(f'Error details saved to {log}', file=sys.stderr, flush=True)
        except OSError:
            pass
        if sys.stdin and sys.stdin.isatty():
            input('Press Enter to close...')
        sys.exit(1)
