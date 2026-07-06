"""Tests for OAuth token management"""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from oauth import is_headless, get_ssh_port_forward_command, get_ssh_host_hint


@pytest.fixture
def oauth(tmp_path, monkeypatch):
    from oauth import OAuth

    config_dir = tmp_path / ".config" / "spotify-to-mp3"
    config_dir.mkdir(parents=True)
    token_file = config_dir / "token.json"

    oauth = OAuth("client_id", "client_secret")
    oauth.token_file = token_file
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return oauth


def test_save_token_sets_expires_at(oauth):
    oauth._save_token({"access_token": "abc", "expires_in": 3600, "refresh_token": "rt"})

    data = json.loads(oauth.token_file.read_text())
    assert "expires_at" in data
    assert data["expires_at"] > time.time()


def test_is_token_expired_missing_expires_at(oauth):
    assert oauth._is_token_expired({"access_token": "abc"}) is True


def test_is_token_expired_valid_token(oauth):
    token = {"access_token": "abc", "expires_at": time.time() + 3600}
    assert oauth._is_token_expired(token) is False


def test_is_token_expired_near_expiry(oauth):
    token = {"access_token": "abc", "expires_at": time.time() + 100}
    assert oauth._is_token_expired(token) is True


def test_authenticate_uses_cached_token(oauth):
    oauth._save_token({"access_token": "cached", "expires_in": 3600, "refresh_token": "rt"})

    with patch.object(oauth, "_validate_access_token", return_value=True):
        with patch.object(oauth, "_refresh_token") as mock_refresh:
            token = oauth.authenticate()
            assert token == "cached"
            mock_refresh.assert_not_called()


def test_authenticate_refreshes_when_api_rejects_cached_token(oauth):
    oauth._save_token({"access_token": "stale", "expires_in": 3600, "refresh_token": "rt"})

    with patch.object(oauth, "_validate_access_token", return_value=False):
        with patch.object(
            oauth, "_refresh_token", return_value={"access_token": "new", "expires_in": 3600, "refresh_token": "rt"}
        ) as mock_refresh:
            token = oauth.authenticate()
            assert token == "new"
            mock_refresh.assert_called_once_with("rt")


def test_authenticate_refreshes_expired_token(oauth):
    oauth.token_file.write_text(
        json.dumps({"access_token": "old", "expires_at": time.time() - 100, "refresh_token": "rt"})
    )

    with patch.object(
        oauth, "_refresh_token", return_value={"access_token": "new", "expires_in": 3600, "refresh_token": "rt"}
    ) as mock_refresh:
        token = oauth.authenticate()
        assert token == "new"
        mock_refresh.assert_called_once_with("rt")

    saved = json.loads(oauth.token_file.read_text())
    assert saved["access_token"] == "new"
    assert "expires_at" in saved


def test_incremental_sync_stops_at_known_page(oauth):
    oauth._save_sync_state({"id1", "id2"})

    page1 = {
        "items": [
            {"track": {"id": "id1", "type": "track", "name": "A", "artists": [{"name": "X"}], "duration_ms": 1000}},
            {"track": {"id": "id2", "type": "track", "name": "B", "artists": [{"name": "Y"}], "duration_ms": 2000}},
        ],
        "next": "http://example.com/next",
    }

    with patch.object(oauth, "authenticate", return_value="token"):
        with patch("oauth.requests.get") as mock_get:
            mock_get.return_value = MagicMock(json=lambda: page1, raise_for_status=lambda: None)
            tracks = oauth.get_all_liked_songs(incremental=True)

    assert tracks == []
    mock_get.assert_called_once()


def test_incremental_sync_returns_new_tracks(oauth):
    oauth._save_sync_state({"id1"})

    page1 = {
        "items": [
            {
                "track": {
                    "id": "id_new",
                    "type": "track",
                    "name": "New",
                    "artists": [{"name": "Z"}],
                    "duration_ms": 3000,
                }
            },
            {"track": {"id": "id1", "type": "track", "name": "Old", "artists": [{"name": "X"}], "duration_ms": 1000}},
        ],
        "next": None,
    }

    with patch.object(oauth, "authenticate", return_value="token"):
        with patch("oauth.requests.get") as mock_get:
            mock_get.return_value = MagicMock(json=lambda: page1, raise_for_status=lambda: None)
            tracks = oauth.get_all_liked_songs(incremental=True)

    assert len(tracks) == 1
    assert tracks[0]["id"] == "id_new"

    state = json.loads(oauth._sync_state_file.read_text())
    assert "id_new" in state["synced_ids"]
    assert "id1" in state["synced_ids"]


def test_is_headless_ssh_without_display(monkeypatch):
    monkeypatch.delenv("SPOTIFY_OAUTH_HEADLESS", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "1.2.3.4 1234 5.6.7.8 22")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert is_headless() is True


def test_is_headless_local_desktop(monkeypatch):
    monkeypatch.delenv("SPOTIFY_OAUTH_HEADLESS", raising=False)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert is_headless() is False


def test_interactive_auth_prints_url_in_headless(oauth, monkeypatch):
    monkeypatch.setattr("oauth.is_headless", lambda: True)

    with patch.object(oauth, "_start_local_server") as mock_server:
        mock_httpd = MagicMock()
        mock_server.return_value = (mock_httpd, {"code": "authcode"})
        with patch.object(
            oauth,
            "_exchange_code_for_token",
            return_value={"access_token": "new", "expires_in": 3600, "refresh_token": "rt"},
        ):
            with patch("oauth.webbrowser.open") as mock_browser:
                oauth._interactive_authenticate()
                mock_browser.assert_not_called()

    mock_httpd.shutdown.assert_called_once()


def test_authenticate_non_interactive_raises_without_token(oauth):
    with pytest.raises(RuntimeError, match="refresh failed"):
        oauth.authenticate(interactive=False)


def test_ssh_port_forward_command(monkeypatch):
    monkeypatch.setenv("USER", "pi")
    monkeypatch.setenv("SSH_CONNECTION", "192.168.1.5 52341 192.168.1.100 22")
    with patch("oauth.getpass.getuser", return_value="pi"):
        cmd = get_ssh_port_forward_command(8888)
    assert cmd == "ssh -L 8888:127.0.0.1:8888 pi@192.168.1.100"


def test_ssh_host_hint_falls_back_to_hostname(monkeypatch):
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    with patch("oauth.socket.gethostname", return_value="raspberrypi"):
        assert get_ssh_host_hint() == "raspberrypi"
