"""Start the dashboard and open a browser."""
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent
URL = 'http://localhost:8501'


def ready():
    try:
        with urllib.request.urlopen(URL + '/_stcore/health', timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


def main():
    if ready():
        webbrowser.open(URL)
        print('Dashboard already running:', URL)
        return
    process = subprocess.Popen([sys.executable, '-m', 'streamlit', 'run',
                                str(ROOT / 'dashboard.py'), '--server.port', '8501'], cwd=ROOT)
    try:
        for _ in range(60):
            if process.poll() is not None:
                raise SystemExit(process.returncode)
            if ready():
                webbrowser.open(URL)
                print('Dashboard ready. Keep this window open. Ctrl+C stops it.')
                break
            time.sleep(0.5)
        else:
            print('Server still starting. Open', URL, 'when ready.')
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait(timeout=10)


if __name__ == '__main__':
    main()
