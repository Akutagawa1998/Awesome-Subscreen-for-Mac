#!/usr/bin/env python3
"""Build a relocatable launcher whose web sources remain editable in this checkout."""
import json
from pathlib import Path
import plistlib
import shutil
import subprocess

root = Path(__file__).resolve().parents[1]
app = root / 'build/Wokyis Console.app'
macos = app / 'Contents/MacOS'
resources = app / 'Contents/Resources'
macos.mkdir(parents=True, exist_ok=True)
resources.mkdir(parents=True, exist_ok=True)
with (app / 'Contents/Info.plist').open('wb') as f:
    plistlib.dump({'CFBundleIdentifier': 'local.wokyis.console', 'CFBundleName': 'Wokyis Console',
                  'CFBundleDisplayName': 'Wokyis Console', 'CFBundleExecutable': 'Wokyis',
                  'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
                  'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
                  'NSAppTransportSecurity': {'NSAllowsLocalNetworking': True}}, f)
(resources / 'config.json').write_text(json.dumps({'root': str(root), 'python': shutil.which('python3')}))
subprocess.run(['swiftc', str(root / 'native/Wokyis.swift'), '-o', str(macos / 'Wokyis'),
                '-module-cache-path', '/tmp/wokyis-swift-module-cache', '-framework', 'Cocoa', '-framework', 'WebKit'], check=True)
subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
print(app)
