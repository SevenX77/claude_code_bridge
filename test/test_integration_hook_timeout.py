"""验证 hook 命令的 /usr/bin/timeout 5s 包装能 cap 住挂死的 hook 子进程。"""
import os
import subprocess
import time
from pathlib import Path

import pytest


@pytest.mark.integration
def test_timeout_wrapper_kills_sleeping_hook_in_5s(tmp_path: Path) -> None:
    if not Path('/usr/bin/timeout').exists():
        pytest.skip("/usr/bin/timeout not present on this OS")
    
    fake_hook = tmp_path / "fake_hook.sh"
    fake_hook.write_text("#!/bin/sh\nsleep 30\n")
    fake_hook.chmod(0o755)
    
    start = time.time()
    proc = subprocess.run(
        ['/usr/bin/timeout', '5', str(fake_hook)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    elapsed = time.time() - start
    
    assert elapsed < 7, f"timeout wrapper took too long: {elapsed:.1f}s"
    assert proc.returncode in (124, 137, 143), f"unexpected rc {proc.returncode}"
