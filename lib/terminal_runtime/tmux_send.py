from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from typing import Callable


class CcbDeliveryError(RuntimeError):
    """Raised when tmux paste+Enter failed to submit input even after retries.

    Gated by ``CCB_VERIFY_DELIVERY=1``; disabled by default so stock CCB
    behaviour is byte-identical when this feature is off.
    """


@dataclass
class TmuxTextSender:
    tmux_run_fn: Callable[..., object]
    looks_like_tmux_target_fn: Callable[[str], bool]
    ensure_not_in_copy_mode_fn: Callable[[str], None]
    build_buffer_name_fn: Callable[..., str]
    sanitize_text_fn: Callable[[str], str]
    should_use_inline_legacy_send_fn: Callable[..., bool]
    env_float_fn: Callable[[str, float], float]
    os_getpid_fn: Callable[[], int] = os.getpid
    time_fn: Callable[[], float] = time.time
    randint_fn: Callable[[int, int], int] = random.randint
    sleep_fn: Callable[[float], None] = time.sleep

    def send_text(self, pane_id: str, text: str) -> None:
        sanitized = self.sanitize_text_fn(text)
        if not sanitized:
            return

        target_is_tmux = self.looks_like_tmux_target_fn(pane_id)
        if not target_is_tmux:
            session = pane_id
            if self.should_use_inline_legacy_send_fn(target_is_tmux=target_is_tmux, text=sanitized):
                self.tmux_run_fn(['send-keys', '-t', session, '-l', sanitized], check=True)
                self.tmux_run_fn(['send-keys', '-t', session, 'Enter'], check=True)
                return
            self._paste_via_buffer(target=session, text=sanitized, pane_target=False)
            return

        self.ensure_not_in_copy_mode_fn(pane_id)
        self._paste_via_buffer(target=pane_id, text=sanitized, pane_target=True)

    def _paste_via_buffer(self, *, target: str, text: str, pane_target: bool) -> None:
        buffer_name = self.build_buffer_name_fn(
            pid=self.os_getpid_fn(),
            now_ms=int(self.time_fn() * 1000),
            rand_int=self.randint_fn(1000, 9999),
        )
        self.tmux_run_fn(['load-buffer', '-b', buffer_name, '-'], check=True, input_bytes=text.encode('utf-8'))
        try:
            verify = pane_target and self._verify_delivery_enabled()
            pre_fp = self._capture_pane_fingerprint(target) if verify else ''

            if pane_target:
                self.tmux_run_fn(['paste-buffer', '-p', '-t', target, '-b', buffer_name], check=True)
            else:
                self.tmux_run_fn(['paste-buffer', '-t', target, '-b', buffer_name, '-p'], check=True)
            enter_delay = self.env_float_fn('CCB_TMUX_ENTER_DELAY', 0.5)
            if enter_delay:
                self.sleep_fn(enter_delay)
            self.tmux_run_fn(['send-keys', '-t', target, 'Enter'], check=True)

            # Second-Enter fallback for cold-start CLIs that swallow the first
            # Enter (bracketed-paste race, input loop not yet ready, paste-confirm
            # dialog). If the first Enter already submitted, the input box is
            # empty and a second Enter is a harmless no-op on Gemini/Codex/Claude.
            # Disabled by default (delay=0); opt in via CCB_TMUX_SECOND_ENTER_DELAY.
            second_enter_delay = self.env_float_fn('CCB_TMUX_SECOND_ENTER_DELAY', 0.0)
            if second_enter_delay > 0:
                self.sleep_fn(second_enter_delay)
                self.tmux_run_fn(['send-keys', '-t', target, 'Enter'], check=False)

            if verify and pre_fp:
                self._verify_delivery(target, pre_fp)
        finally:
            self.tmux_run_fn(['delete-buffer', '-b', buffer_name], check=False)

    def _verify_delivery_enabled(self) -> bool:
        """CCB_VERIFY_DELIVERY=1 (any positive number) opts into post-send verification."""
        return self.env_float_fn('CCB_VERIFY_DELIVERY', 0.0) > 0.0

    def _capture_pane_fingerprint(self, pane_id: str, lines: int = 3) -> str:
        """Last N non-empty visible lines of the pane.

        Used to detect whether prompt changed after Enter (empty fingerprint
        = capture failed → verification skipped softly).
        """
        try:
            cp = self.tmux_run_fn(
                ['capture-pane', '-p', '-t', pane_id],
                capture=True,
                timeout=2.0,
                check=False,
            )
        except Exception:
            return ''
        if cp is None:
            return ''
        text = getattr(cp, 'stdout', cp if isinstance(cp, (str, bytes)) else '')
        if isinstance(text, bytes):
            try:
                text = text.decode('utf-8', errors='replace')
            except Exception:
                return ''
        if not isinstance(text, str) or not text:
            return ''
        non_empty = [ln for ln in text.splitlines() if ln.strip()]
        return '\n'.join(non_empty[-lines:])

    def _verify_delivery(self, pane_id: str, pre_fp: str) -> None:
        """Confirm Enter submitted the paste; retry alternate keycodes on failure."""
        post_delay_s = self.env_float_fn('CCB_VERIFY_POST_DELAY_MS', 300.0) / 1000.0
        self.sleep_fn(post_delay_s)
        post_fp = self._capture_pane_fingerprint(pane_id)
        # Prompt changed (success) OR post-capture failed (soft skip — can't confirm).
        if not post_fp or post_fp != pre_fp:
            return

        retry_env = os.environ.get('CCB_VERIFY_RETRY_KEYCODES', 'Return,C-m').strip()
        retry_keys = [k.strip() for k in retry_env.split(',') if k.strip()]
        for key in retry_keys:
            try:
                self.tmux_run_fn(['send-keys', '-t', pane_id, key], check=False)
            except Exception:
                continue
            self.sleep_fn(post_delay_s)
            if self._capture_pane_fingerprint(pane_id) != pre_fp:
                return

        raise CcbDeliveryError(
            "tmux delivery verify: pane {pane} prompt unchanged after Enter + retries {keys}. "
            "Last pane tail:\n{fp}".format(pane=pane_id, keys=retry_keys, fp=pre_fp)
        )
