"""One-click local runner startup; reuses a runner already on port 49848."""
import json
import logging
import webbrowser
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError
from windows_client.server import create_server

ROOT=Path(__file__).resolve().parents[1]
URL='http://127.0.0.1:49848/'


def main():
    try:
        with urlopen(URL+'api/runner',timeout=2) as response:
            data=json.load(response)
        if 'settings' in data and 'jobs' in data:
            webbrowser.open(URL)
            return
    except (URLError,ValueError,OSError):
        pass
    (ROOT/'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(),logging.FileHandler(ROOT/'logs/runner.log',encoding='utf-8')])
    server=create_server(ROOT/'scans/received',port=49848)
    logging.info('Scanner ready: %s. Keep this window open; Ctrl+C stops the server.',URL)
    webbrowser.open(URL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.app.executor.shutdown(wait=True)


if __name__=='__main__':
    main()
