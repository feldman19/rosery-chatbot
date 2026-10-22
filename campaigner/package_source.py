"""Package an explicit source allowlist. Never include runtime state or credentials."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parent
out = root.parent / 'outputs' / 'campaigner-reviewed-source-2026-10-16.zip'
files = [p for p in root.glob('*.py') if not p.name.startswith('update_ui_')]
files += [root / name for name in ('README.txt', 'requirements.txt', 'manager.html', 'dashboard.html', 'pilot.html', 'pilot-ui.js', 'registration-ui.js', 'review-ui.js', 'connection-ui.js', 'live-ui.js', 'operator-ui.js', 'launch-ui.js', 'strategy-ui.js', 'work-home.js', 'test_work_home.js', 'test_strategy_ui.js', 'test_launch_ui.js', 'meta-execution-panel.js', 'test_meta_execution_panel.js', 'decision-ui.js', 'pilot-format.js', 'test_pilot_format.js', 'demo-data.json', 'evidence-example.json')]
for directory in ('core', 'evidence', 'assistant', 'autonomy', 'tests'):
    files += [p for p in (root / directory).rglob('*') if p.is_file() and p.suffix in ('.py', '.txt', '.json') and '__pycache__' not in p.parts]
manifest = []
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(files)):
        data = path.read_bytes()
        name = path.relative_to(root).as_posix()
        archive.writestr('campaigner/' + name, data)
        manifest.append({'path': name, 'sha256': hashlib.sha256(data).hexdigest()})
    archive.writestr('SOURCE_MANIFEST.json', json.dumps(manifest, indent=2))
print(json.dumps({'files': len(manifest), 'archive': str(out), 'sha256': hashlib.sha256(out.read_bytes()).hexdigest()}))
