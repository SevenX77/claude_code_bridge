"""集成测试：需要 ccbd 已启动 (ccb ps 有 mounted 状态) + a2 是 Gemini。
本测试如果环境不满足会 SKIP，不会 hard fail。
"""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest


@pytest.mark.integration
def test_gemini_reception_artifact_appears_within_5s(tmp_path: Path) -> None:
    workspace = os.environ.get('CCB_TEST_WORKSPACE', str(Path.home()))
    ccbd_root = Path(workspace) / '.ccb'
    if not (ccbd_root / 'ccbd').exists():
        pytest.skip("ccbd not mounted; set CCB_TEST_WORKSPACE or start ccbd manually")
    
    reception_dir = ccbd_root / 'agents' / 'a2' / 'provider-runtime' / 'gemini' / 'reception'
    if not reception_dir.exists():
        pytest.skip(f"reception_dir missing: {reception_dir}; install_gemini_hooks may not have run")
    
    unique = f"reception-test-{uuid.uuid4().hex[:8]}"
    proc = subprocess.run(
        ['ccb', 'ask', '--wait', '--timeout', '60', 'a2', f'echo {unique}'],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=90,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    
    job_id = None
    for line in proc.stdout.splitlines():
        if 'job_id:' in line:
            job_id = line.split(':', 1)[1].strip()
            break
    assert job_id, f"job_id not found in ask output: {proc.stdout[:500]}"
    
    artifact = reception_dir / 'events' / f'{job_id}.json'
    deadline = time.time() + 5
    while time.time() < deadline:
        if artifact.exists():
            data = json.loads(artifact.read_text())
            assert data['req_id'] == job_id
            return
        time.sleep(0.2)
    
    pytest.fail(f"reception artifact never appeared at {artifact}")
