from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

_SLASH_COMMAND_RE = re.compile(r'^/(?:clear|new|help|auth)(?:\s+.*)?$')
_SLASH_SEND_KEYS_DELAY_S = 0.25


def _is_slash_command(text: str) -> bool:
    return _slash_command_text(text) is not None


def _slash_command_text(text: str) -> str | None:
    candidate = text.strip()
    if not candidate or '\n' in candidate or '\r' in candidate:
        return None
    if _SLASH_COMMAND_RE.fullmatch(candidate):
        return candidate
    return None


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

    def send_text(
        self,
        pane_id: str,
        text: str,
        *,
        req_id: str | None = None,
        reception_dir: Path | None = None,
    ) -> None:
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
        slash_command = _slash_command_text(sanitized)
        if slash_command:
            self.tmux_run_fn(['send-keys', '-t', pane_id, '-l', slash_command], check=True)
            self.sleep_fn(_SLASH_SEND_KEYS_DELAY_S)
            self.tmux_run_fn(['send-keys', '-t', pane_id, 'Enter'], check=True)
            return

        self._paste_via_buffer(
            target=pane_id,
            text=sanitized,
            pane_target=True,
            req_id=req_id,
            reception_dir=reception_dir,
        )

    def _is_slash_command(self, text: str) -> bool:
        return _is_slash_command(text)

    def _slash_command_text(self, text: str) -> str | None:
        return _slash_command_text(text)

    def _paste_via_buffer(
        self,
        *,
        target: str,
        text: str,
        pane_target: bool,
        req_id: str | None = None,
        reception_dir: Path | None = None,
    ) -> None:
        # Kill switch or missing params → legacy path
        if (
            req_id is None
            or reception_dir is None
            or not pane_target
            or self.env_float_fn('CCB_RECEPTION_DRIVEN', 1.0) <= 0
        ):
            return self._paste_via_buffer_legacy(target=target, text=text, pane_target=pane_target)
        return self._paste_via_buffer_reception_driven(
            target=target,
            text=text,
            req_id=req_id,
            reception_dir=reception_dir,
        )

    def _paste_via_buffer_legacy(self, *, target: str, text: str, pane_target: bool) -> None:
        """Original _paste_via_buffer implementation, moved here unchanged."""
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

    def _paste_via_buffer_reception_driven(
        self,
        *,
        target: str,
        text: str,
        req_id: str,
        reception_dir: Path,
    ) -> None:
        """Reception-driven paste loop implementing D3 invariant.

        D3 invariant (MUST hold at every poll iteration head):
          reception file OR pane_shows_agent_activity → immediately return, never retry.
          Only when BOTH are absent do we enter the diagnostic branch to check req_id.
          break (triggering retry) fires ONLY when both signals absent AND req_id not in last 10 lines.

        This is the sole defence against double-prompt injection when hook fail-open occurs.
        """
        from terminal_runtime.agent_activity import pane_shows_agent_activity

        poll_interval = self.env_float_fn('CCB_RECEPTION_POLL_INTERVAL_S', 2.0)
        timeout_s = self.env_float_fn('CCB_RECEPTION_TIMEOUT_S', 60.0)
        max_attempts = int(self.env_float_fn('CCB_RECEPTION_MAX_ATTEMPTS', 3.0))
        enter_delay = self.env_float_fn('CCB_TMUX_ENTER_DELAY', 0.5)
        second_enter_delay = self.env_float_fn('CCB_TMUX_SECOND_ENTER_DELAY', 0.0)
        reception_path = Path(reception_dir).expanduser() / 'events' / f'{req_id}.json'
        last_tail = ''

        for attempt in range(max(1, max_attempts)):
            if attempt > 0:
                # Clear any partial / mid-state input before retrying (D5)
                self.tmux_run_fn(['send-keys', '-t', target, 'Escape'], check=False)
                self.sleep_fn(0.05)
                self.tmux_run_fn(['send-keys', '-t', target, 'C-u'], check=False)
                self.sleep_fn(0.05)

            # paste
            buffer_name = self.build_buffer_name_fn(
                pid=self.os_getpid_fn(),
                now_ms=int(self.time_fn() * 1000),
                rand_int=self.randint_fn(1000, 9999),
            )
            self.tmux_run_fn(
                ['load-buffer', '-b', buffer_name, '-'],
                check=True,
                input_bytes=text.encode('utf-8'),
            )
            try:
                self.tmux_run_fn(
                    ['paste-buffer', '-p', '-t', target, '-b', buffer_name],
                    check=True,
                )
                if enter_delay:
                    self.sleep_fn(enter_delay)
                self.tmux_run_fn(['send-keys', '-t', target, 'Enter'], check=True)
                if second_enter_delay > 0:
                    self.sleep_fn(second_enter_delay)
                    self.tmux_run_fn(['send-keys', '-t', target, 'Enter'], check=False)
            finally:
                self.tmux_run_fn(['delete-buffer', '-b', buffer_name], check=False)

            # Poll loop — D3 invariant:
            #   reception file OR pane agent activity → already delivered → return immediately, no retry
            #   Both absent → diagnostic: check req_id in tail
            #   break (→ next attempt) ONLY when both absent AND req_id not in last 10 lines
            #
            # This is the sole defence against hook fail-open double-prompt injection.
            # If markers are missed here, the safeguard collapses. Maintain AGENT_ACTIVITY_MARKERS carefully.
            deadline = self.time_fn() + timeout_s
            while self.time_fn() < deadline:
                tail = self._capture_pane_tail(target, lines=10)
                last_tail = tail

                # D3: either signal present → delivered, return immediately
                if reception_path.exists() or pane_shows_agent_activity(tail):
                    return

                # Both absent: check if req_id reached the pane
                if req_id in tail:
                    # Text reached pane but Enter was swallowed → send补Enter
                    self.tmux_run_fn(['send-keys', '-t', target, 'Enter'], check=False)
                else:
                    # paste did not reach pane at all → break to retry
                    break

                self.sleep_fn(poll_interval)

        raise CcbDeliveryError(
            f"reception not confirmed for req_id={req_id} after {max_attempts} attempts. "
            f"Last pane tail:\n{last_tail}"
        )

    def _capture_pane_tail(self, pane_id: str, *, lines: int = 10) -> str:
        """capture-pane -p -S -<lines>; return decoded text or ''."""
        try:
            cp = self.tmux_run_fn(
                ['capture-pane', '-p', '-S', f'-{lines}', '-t', pane_id],
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
        return text if isinstance(text, str) else ''

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
