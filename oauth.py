"""
Spotify OAuth token management for accessing user data (liked songs, etc.)
Handles OAuth 2.0 authorization code flow with PKCE.
"""

import base64
import getpass
import hashlib
import http.server
import json
import os
import secrets
import socket
import socketserver
import string
import time
import webbrowser
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

import requests


def get_ssh_host_hint() -> str:
    """Return the best hostname/IP for ssh user@host to reach this machine."""
    hosts: list[str] = []

    ssh_conn = os.environ.get("SSH_CONNECTION", "").split()
    if len(ssh_conn) >= 3:
        server_addr = ssh_conn[2]
        if server_addr and server_addr not in ("127.0.0.1", "::1"):
            hosts.append(server_addr)

    hostname = socket.gethostname()
    if hostname and hostname not in hosts:
        hosts.append(hostname)

    return hosts[0] if hosts else "localhost"


def get_ssh_port_forward_command(port: int = 8888) -> str:
    """Build an ssh -L command using the current user and host."""
    user = getpass.getuser()
    host = get_ssh_host_hint()
    return f"ssh -L {port}:127.0.0.1:{port} {user}@{host}"


def is_headless() -> bool:
    """Detect environments where opening a browser locally won't work (SSH, Pi, CI)."""
    override = os.environ.get("SPOTIFY_OAUTH_HEADLESS", "").lower()
    if override in ("1", "true", "yes"):
        return True
    if override in ("0", "false", "no"):
        return False

    if os.environ.get("SSH_CONNECTION") and not os.environ.get("DISPLAY"):
        return True

    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return True

    return False


class OAuth:
    """Handle Spotify OAuth 2.0 authentication for user-specific endpoints"""

    def __init__(self, client_id, client_secret, redirect_uri="http://127.0.0.1:8888/callback"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = Path.home() / ".config" / "spotify-to-mp3" / "token.json"

    def verify_credentials(self):
        """Verify that OAuth credentials work by refreshing or using cached token"""
        try:
            self.authenticate(interactive=False)
            print("✅ OAuth credentials verified")
            return True
        except Exception as e:
            print(f"❌ OAuth credential verification failed: {e}")
            return False

    def _validate_access_token(self, access_token: str) -> bool:
        """Check access token against Spotify API (catches mis-saved expiry metadata)."""
        try:
            response = requests.get(
                "https://api.spotify.com/v1/me", headers={"Authorization": f"Bearer {access_token}"}, timeout=10
            )
            return response.status_code == 200
        except requests.RequestException:
            return False

    def _try_refresh(self, refresh_token: str) -> str | None:
        """Refresh an expired token. Returns access token or None on failure."""
        try:
            print("🔄 Refreshing Spotify token...")
            new_token_data = self._refresh_token(refresh_token)
            self._save_token(new_token_data)
            print("✅ Token refreshed")
            return new_token_data["access_token"]
        except Exception as e:
            print(f"⚠️  Token refresh failed: {e}")
            return None

    def _generate_code_verifier(self):
        """Generate a code verifier for PKCE"""
        code_verifier = base64.urlsafe_b64encode(os.urandom(40)).decode("utf-8")
        code_verifier = code_verifier.rstrip("=")
        return code_verifier

    def _generate_code_challenge(self, code_verifier):
        """Generate code challenge from verifier using S256 method"""
        sha256 = hashlib.sha256(code_verifier.encode("utf-8")).digest()
        code_challenge = base64.urlsafe_b64encode(sha256).decode("utf-8")
        code_challenge = code_challenge.rstrip("=")
        return code_challenge

    def _get_auth_url(self, code_challenge, state):
        """Generate Spotify authorization URL"""
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "scope": "user-library-read",
            "redirect_uri": self.redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"https://accounts.spotify.com/authorize?{urlencode(params)}"

    def _start_local_server(self, port=8888):
        """Start a simple HTTP server to receive the callback"""

        # Store auth code in closure
        auth_result: dict[str, str | None] = {"code": None}

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/callback"):
                    # Extract authorization code from query params
                    parsed = urlparse(self.path)
                    params = parse_qs(parsed.query)

                    if "code" in params:
                        auth_result["code"] = params["code"][0]
                        self.send_response(200)
                        self.send_header("Content-type", "text/html")
                        self.end_headers()
                        self.wfile.write(b"Authentication successful! You can close this window.")
                    else:
                        self.send_response(400)
                        self.send_header("Content-type", "text/html")
                        self.end_headers()
                        self.wfile.write(b"Error: No authorization code received.")
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):  # type: ignore[override]
                # Suppress logging
                pass

        handler = CallbackHandler
        httpd = socketserver.TCPServer(("", port), handler)

        # Don't use daemon thread - keep server alive
        server_thread = Thread(target=httpd.serve_forever)
        server_thread.daemon = False
        server_thread.start()

        return httpd, auth_result

    def authenticate(self, interactive: bool = True) -> str:
        """Return a valid access token, refreshing cached tokens when needed."""

        if self.token_file.exists():
            token_data = self._load_token()
            if token_data:
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token")

                if access_token and not self._is_token_expired(token_data):
                    if self._validate_access_token(access_token):
                        print("✅ Using cached Spotify token")
                        return access_token
                    print("⚠️  Cached token rejected by Spotify API")

                if refresh_token:
                    refreshed = self._try_refresh(refresh_token)
                    if refreshed:
                        return refreshed

        if not interactive:
            raise RuntimeError(
                "OAuth token expired and refresh failed. "
                "Re-authenticate on a machine with a browser, or run with SSH port forwarding."
            )

        return self._interactive_authenticate()

    def _interactive_authenticate(self) -> str:
        """Full browser OAuth flow (opens browser or prints URL in headless mode)."""
        print("🔐 Authenticating with Spotify...")

        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        auth_url = self._get_auth_url(code_challenge, state)

        headless = is_headless()
        parsed_redirect = urlparse(self.redirect_uri)
        port = parsed_redirect.port or 8888
        callback_url = f"http://127.0.0.1:{port}/callback"

        if headless:
            ssh_cmd = get_ssh_port_forward_command(port)
            print("\n" + "=" * 60)
            print("HEADLESS MODE — open this URL in your browser:")
            print(auth_url)
            print()
            if os.environ.get("SSH_CONNECTION"):
                print("Reconnect your SSH session with port forwarding:")
            else:
                print("From your desktop, connect with port forwarding:")
            print(f"  {ssh_cmd}")
            print()
            print(f"Waiting for callback on {callback_url} ...")
            print("=" * 60 + "\n")
        else:
            print("Opening browser for Spotify authentication...")
            print(f"If browser doesn't open, visit: {auth_url}")
            webbrowser.open(auth_url)

        port = parsed_redirect.port or 8888
        try:
            httpd, auth_result = self._start_local_server(port)

            timeout = 120
            start_time = time.time()

            while time.time() - start_time < timeout:
                if auth_result["code"]:
                    auth_code = auth_result["code"]
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("Authentication timed out. Please try again.")

            httpd.shutdown()

        except Exception as e:
            raise RuntimeError(f"Failed to receive authentication callback: {e}") from e

        token_data = self._exchange_code_for_token(auth_code, code_verifier)
        self._save_token(token_data)

        return token_data["access_token"]

    def _exchange_code_for_token(self, code, code_verifier):
        """Exchange authorization code for access token"""
        url = "https://accounts.spotify.com/api/token"

        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {"Authorization": f"Basic {encoded_credentials}", "Content-Type": "application/x-www-form-urlencoded"}

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        return response.json()

    def _refresh_token(self, refresh_token):
        """Refresh an expired access token"""
        url = "https://accounts.spotify.com/api/token"

        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {"Authorization": f"Basic {encoded_credentials}", "Content-Type": "application/x-www-form-urlencoded"}

        data = {"grant_type": "refresh_token", "refresh_token": refresh_token}

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        new_token_data = response.json()
        # Keep the old refresh token if a new one isn't provided
        if "refresh_token" not in new_token_data:
            new_token_data["refresh_token"] = refresh_token

        return new_token_data

    def _load_token(self):
        """Load token from file"""
        try:
            with open(self.token_file, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return None

    def _save_token(self, token_data):
        """Save token to file with calculated expires_at timestamp"""
        if "expires_in" in token_data:
            token_data["expires_at"] = time.time() + token_data["expires_in"]

        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_file, "w") as f:
            json.dump(token_data, f, indent=2)

    def _is_token_expired(self, token_data):
        """Check if token is expired or about to expire"""
        expires_at = token_data.get("expires_at")
        if expires_at is None:
            return True

        # Consider token expired if less than 5 minutes remaining
        return time.time() > (expires_at - 300)

    @property
    def _sync_state_file(self):
        return self.token_file.parent / "liked-sync.json"

    def _load_sync_state(self):
        """Load set of previously synced liked-song Spotify IDs"""
        try:
            with open(self._sync_state_file, "r") as f:
                data = json.load(f)
            return set(data.get("synced_ids", []))
        except (json.JSONDecodeError, FileNotFoundError):
            return set()

    def _save_sync_state(self, synced_ids):
        """Persist synced liked-song Spotify IDs"""
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._sync_state_file, "w") as f:
            json.dump({"synced_ids": sorted(synced_ids), "updated_at": time.time()}, f, indent=2)

    def get_liked_songs(self, limit=50, offset=0):
        """Get user's liked/saved tracks"""
        access_token = self.authenticate()

        url = "https://api.spotify.com/v1/me/tracks"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "limit": min(limit, 50),  # Max 50 per request
            "offset": offset,
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        return response.json()

    def get_all_liked_songs(self, incremental=True, max_tracks=None):
        """Get liked songs with pagination.

        When incremental=True (default), stops fetching once a page contains only
        previously synced tracks (API returns newest likes first).
        When max_tracks is set, stops once enough tracks are collected.
        """
        access_token = self.authenticate()

        synced_ids = set() if not incremental else self._load_sync_state()
        fetched_ids: set[str] = set()
        new_tracks = []
        offset = 0
        limit = 50

        if incremental and synced_ids:
            print("📚 Checking for new liked songs...")
        else:
            print("📚 Fetching liked songs...")

        while True:
            url = "https://api.spotify.com/v1/me/tracks"
            headers = {"Authorization": f"Bearer {access_token}"}
            params = {"limit": min(limit, 50), "offset": offset}

            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

            page_track_ids: list[str] = []

            for item in data["items"]:
                track = item["track"]
                if track and track["type"] == "track":
                    track_id = track["id"]
                    page_track_ids.append(track_id)
                    fetched_ids.add(track_id)

                    if track_id not in synced_ids:
                        artists = ", ".join([artist["name"] for artist in track["artists"]])
                        new_tracks.append(
                            {
                                "id": track_id,
                                "name": track["name"],
                                "artists": artists,
                                "duration_ms": track["duration_ms"],
                                "popularity": track.get("popularity", 0),
                                "album": track.get("album", {}),
                                "added_at": item.get("added_at"),
                            }
                        )

            print(f"  Fetched {len(fetched_ids)} tracks ({len(new_tracks)} new)...")

            if max_tracks and len(new_tracks) >= max_tracks:
                new_tracks = new_tracks[:max_tracks]
                break

            if incremental and synced_ids and page_track_ids and all(tid in synced_ids for tid in page_track_ids):
                print("  Reached previously synced tracks, stopping early")
                break

            if data["next"]:
                offset += limit
            else:
                break

        self._save_sync_state(synced_ids | fetched_ids)

        if incremental and synced_ids:
            print(f"✅ Found {len(new_tracks)} new liked songs")
        else:
            print(f"✅ Found {len(new_tracks)} liked songs")
        return new_tracks
