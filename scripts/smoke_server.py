"""Live local HTTP + Bokeh WebSocket smoke check. No browser UI automation."""
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from bokeh.client import pull_session


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as tmp:
        process=subprocess.Popen([sys.executable,'-m','signal_annotation','--demo','--no-browser',
                                  '--demo-dir',tmp,'--port',str(port)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            url=f'http://127.0.0.1:{port}/'
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError(process.stdout.read())
                try:
                    with urllib.request.urlopen(url,timeout=.5) as response: html=response.read().decode()
                    break
                except OSError: time.sleep(.1)
            else: raise RuntimeError('Server did not start within 10 seconds')
            assert 'cdn.bokeh.org' not in html
            scripts=re.findall(r'<script[^>]+src="([^"]+)"',html)
            assert scripts
            for src in scripts:
                assert src.startswith('/') or src.startswith(url)
                with urllib.request.urlopen(url.rstrip('/')+src if src.startswith('/') else src) as response:
                    assert response.status==200
            with pull_session(url=url) as session:
                doc=session.document
                plots=[doc.get_model_by_name(f'plot_ch{i}') for i in range(6)]
                assert all(plots) and len({p.x_range.id for p in plots})==1
                assert doc.get_model_by_name('mode').value=='navigate'
            print('PASS: local HTTP, local JS resources, live WebSocket document, 6 synchronized channels')
        finally:
            process.terminate()
            try: output=process.communicate(timeout=10)[0]
            except subprocess.TimeoutExpired: process.kill(); output=process.communicate()[0]
            if 'Traceback' in output: raise RuntimeError(output)


if __name__=='__main__': main()
