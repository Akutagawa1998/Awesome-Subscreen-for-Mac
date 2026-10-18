#!/usr/bin/env python3
"""Keep the local service attached to the user's explicit launcher session.

Starting Python here also avoids an unapproved GUI child hanging while macOS
decides whether it may read a checkout under Documents. No TCC settings change.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def service_ready():
    try:
        with urlopen('http://127.0.0.1:8765/api/health', timeout=1) as response:
            if json.load(response).get('app') != 'wokyis-console':
                raise RuntimeError('8765 端口被其他服务占用')
            return True
    except URLError:
        return False


def main():
    child = None
    try:
        if not service_ready():
            child = subprocess.Popen([sys.executable, '-u', str(ROOT/'server.py')], cwd=ROOT)
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError('本地服务未启动，请检查上方的错误信息')
                if service_ready():
                    break
                time.sleep(.1)
            else:
                raise RuntimeError('服务启动超时，请检查 macOS 是否在等待文件访问许可')
        subprocess.run(['open', '-W', str(ROOT/'build/Wokyis Console.app')], check=True)
    finally:
        if child and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
