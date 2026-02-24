"""Tests for DjustMonitorConfig.ready() release auto-detection."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from djust_monitor.apps import _detect_release


class TestDetectRelease:
    def test_env_var_takes_priority(self, monkeypatch):
        monkeypatch.setenv("DJUST_MONITOR_RELEASE", "v1.2.3")
        assert _detect_release() == "v1.2.3"

    def test_env_var_not_set_falls_through_to_git(self, monkeypatch):
        monkeypatch.delenv("DJUST_MONITOR_RELEASE", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "v0.9.0-5-gabcdef\n"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            release = _detect_release()
        assert release == "v0.9.0-5-gabcdef"
        mock_run.assert_called_once_with(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=2,
        )

    def test_git_failure_returns_empty(self, monkeypatch):
        monkeypatch.delenv("DJUST_MONITOR_RELEASE", raising=False)
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            release = _detect_release()
        assert release == ""

    def test_git_oserror_returns_empty(self, monkeypatch):
        monkeypatch.delenv("DJUST_MONITOR_RELEASE", raising=False)
        with patch("subprocess.run", side_effect=OSError("git not found")):
            release = _detect_release()
        assert release == ""

    def test_git_timeout_returns_empty(self, monkeypatch):
        monkeypatch.delenv("DJUST_MONITOR_RELEASE", raising=False)
        with patch(
            "subprocess.run", side_effect=subprocess.TimeoutExpired("git", 2)
        ):
            release = _detect_release()
        assert release == ""

    def test_empty_env_var_falls_through_to_git(self, monkeypatch):
        monkeypatch.setenv("DJUST_MONITOR_RELEASE", "")
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234"
        with patch("subprocess.run", return_value=mock_result):
            release = _detect_release()
        assert release == "abc1234"
