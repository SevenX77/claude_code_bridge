from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_gemini_send_via_terminal_passes_req_id_and_reception_dir(tmp_path: Path) -> None:
    """_send_via_terminal 应从 prompt 抽 req_id + 算 reception_dir 传 backend.send_text。"""
    from provider_backends.gemini.comm_runtime.communicator_facade import GeminiCommunicator

    comm = GeminiCommunicator.__new__(GeminiCommunicator)  # 跳过 __init__，注入字段
    comm.backend = MagicMock()
    comm.pane_id = "%5"
    comm.session = MagicMock()
    completion_dir = tmp_path / "agent" / "provider-runtime" / "gemini" / "completion"
    completion_dir.mkdir(parents=True)
    comm.session.completion_dir = completion_dir

    content = "CCB_REQ_ID: job_testxyz Execute the full request from @/tmp/req.md and reply directly."
    comm._send_via_terminal(content)

    comm.backend.send_text.assert_called_once()
    args, kwargs = comm.backend.send_text.call_args
    assert args[0] == "%5"
    assert args[1] == content
    assert kwargs.get('req_id') == 'job_testxyz'
    assert kwargs.get('reception_dir') == completion_dir.parent / 'reception'


def test_gemini_send_via_terminal_no_reqid_passes_none(tmp_path: Path) -> None:
    """如果 prompt 没 req_id（用户手输），传 None 让 send_text 走旧路径。"""
    from provider_backends.gemini.comm_runtime.communicator_facade import GeminiCommunicator

    comm = GeminiCommunicator.__new__(GeminiCommunicator)
    comm.backend = MagicMock()
    comm.pane_id = "%5"
    comm.session = MagicMock()
    completion_dir = tmp_path / "completion"
    completion_dir.mkdir(parents=True)
    comm.session.completion_dir = completion_dir

    comm._send_via_terminal("plain user prompt no marker")

    _, kwargs = comm.backend.send_text.call_args
    assert kwargs.get('req_id') is None

def test_send_via_terminal_creates_reception_dir_if_missing(tmp_path: Path) -> None:
    """如果 reception_dir 在 send 时不存在，_send_via_terminal 应 defensively 创建它。"""
    from provider_backends.gemini.comm_runtime.communicator_facade import GeminiCommunicator
    comm = GeminiCommunicator.__new__(GeminiCommunicator)
    comm.backend = MagicMock()
    comm.pane_id = "%5"
    comm.session = MagicMock()
    completion_dir = tmp_path / "agent" / "provider-runtime" / "gemini" / "completion"
    completion_dir.mkdir(parents=True)
    # 故意不创建 reception，让 _send_via_terminal 自己 mkdir
    expected_reception = completion_dir.parent / "reception"
    assert not expected_reception.exists(), "前置：reception_dir 应不存在"
    comm.session.completion_dir = completion_dir
    content = "CCB_REQ_ID: job_testxyz Execute the request"
    comm._send_via_terminal(content)
    assert expected_reception.exists(), "_send_via_terminal 应 defensively mkdir reception_dir"
    _, kwargs = comm.backend.send_text.call_args
    assert kwargs.get('reception_dir') == expected_reception
