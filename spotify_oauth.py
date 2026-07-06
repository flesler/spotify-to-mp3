"""
Spotify OAuth token management for accessing user data (liked songs, etc.)
Handles OAuth 2.0 authorization code flow with PKCE.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import socketserver
import string
import webbrowser
from pathlib import Path
from threading import Thread
from urllib.parse import urlencode


class SpotifyOAuth:
    """Handle Spotify OAuth 2.0 authentication for user-specific endpoints"""

    def __init__(self, client_id, client_secret, redirect_uri="http://localhost:8888/callback"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.token_file = Path.home() / ".config" / "spotify-to-mp3" / "token.json"

    def _generate_code_verifier(self):
        """Generate a code verifier for PKCE"""
        code_verifier = base64.urlsafe_b64encode(os.urandom(40)).decode('utf-8')
        code_verifier = code_verifier.rstrip('=')
        return code_verifier

    def _generate_code_challenge(self, code_verifier):
        """Generate code challenge from verifier using S256 method"""
        sha256 = hashlib.sha256(code_verifier.encode('utf-8')).digest()
        code_challenge = base64.urlsafe_b64encode(sha256).decode('utf-8')
        code_challenge = code_challenge.rstrip('=')
        return code_challenge

    def _get_auth_url(self, code_challenge, state):
        """Generate Spotify authorization URL"""
        params = {
            'response_type': 'code',
            'client_id': self.client_id,
            'scope': 'user-library-read',
            'redirect_uri': self.redirect_uri,
            'state': state,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256'
        }
        return f"https://accounts.spotify.com/authorize?{urlencode(params)}"

    def _start_local_server(self, port=8888):
        """Start a simple HTTP server to receive the callback"""
        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def __init__(self, *args, **kwargs):
                self.auth_code = None
                super().__init__(*args, **kwargs)

            def do_GET(self):
                if self.path.startswith('/callback'):
                    # Extract authorization code from query params
                    from urllib.parse import parse_qs, urlparse
                    parsed = urlparse(self.path)
                    params = parse_qs(parsed.query)

                    if 'code' in params:
                        self.auth_code = params['code'][0]
                        self.send_response(200)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(b'Authentication successful! You can close this window.')
                    else:
                        self.send_response(400)
                        self.send_header('Content-type', 'text/html')
                        self.end_headers()
                        self.wfile.write(b'Error: No authorization code received.')
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format, *args):
                # Suppress logging
                pass

        handler = CallbackHandler
        httpd = socketserver.TCPServer(("", port), handler)

        server_thread = Thread(target=httpd.handle_request)
        server_thread.daemon = True
        server_thread.start()

        return httpd, handler

    def authenticate(self):
        """Perform OAuth authentication and return access token"""

        # Load existing token if available
        if self.token_file.exists():
            token_data = self._load_token()
            if token_data and not self._is_token_expired(token_data):
                print("✅ Using cached Spotify token")
                return token_data['access_token']

        print("🔐 Authenticating with Spotify...")

        # Generate PKCE parameters
        code_verifier = self._generate_code_verifier()
        code_challenge = self._generate_code_challenge(code_verifier)
        state = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))

        # Get authorization URL
        auth_url = self._get_auth_url(code_challenge, state)

        # Open browser for user authentication
        print(f"Opening browser for Spotify authentication...")
        print(f"If browser doesn't open, visit: {auth_url}")
        webbrowser.open(auth_url)

        # Start local server to receive callback
        port = 8888
        try:
            httpd, handler_class = self._start_local_server(port)

            # Wait for callback (with timeout)
            import time
            timeout = 120  # 2 minutes
            start_time = time.time()

            while time.time() - start_time < timeout:
                if hasattr(handler_class, 'auth_code') and handler_class.auth_code:
                    auth_code = handler_class.auth_code
                    break
                time.sleep(0.5)
            else:
                raise TimeoutError("Authentication timed out. Please try again.")

            httpd.shutdown()

        except Exception as e:
            raise Exception(f"Failed to receive authentication callback: {e}")

        # Exchange authorization code for tokens
        token_data = self._exchange_code_for_token(auth_code, code_verifier)

        # Save token
        self._save_token(token_data)

        return token_data['access_token']

    def _exchange_code_for_token(self, code, code_verifier):
        """Exchange authorization code for access token"""
        import requests

        url = "https://accounts.spotify.com/api/token"

        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier
        }

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        return response.json()

    def _refresh_token(self, refresh_token):
        """Refresh an expired access token"""
        import requests

        url = "https://accounts.spotify.com/api/token"

        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        new_token_data = response.json()
        # Keep the old refresh token if a new one isn't provided
        if 'refresh_token' not in new_token_data:
            new_token_data['refresh_token'] = refresh_token

        return new_token_data

    def _load_token(self):
        """Load token from file"""
        try:
            with open(self.token_file, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return None

    def _save_token(self, token_data):
        """Save token to file"""
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_file, 'w') as f:
            json.dump(token_data, f, indent=2)

    def _is_token_expired(self, token_data):
        """Check if token is expired or about to expire"""
        import time

        expires_at = token_data.get('expires_at', 0)
        current_time = time.time()

        # Consider token expired if less than 5 minutes remaining
        return current_time > (expires_at - 300)

    def get_liked_songs(self, limit=50, offset=0):
        """Get user's liked/saved tracks"""
        import requests

        access_token = self.authenticate()

        url = "https://api.spotify.com/v1/me/tracks"
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "limit": min(limit, 50),  # Max 50 per request
            "offset": offset
        }

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()

        return response.json()

    def get_all_liked_songs(self):
        """Get all liked songs with pagination"""
        tracks = []
        offset = 0
        limit = 50

        print("📚 Fetching liked songs...")

        while True:
            data = self.get_liked_songs(limit=limit, offset=offset)

            for item in data['items']:
                track = item['track']
                if track and track['type'] == 'track':
                    artists = ", ".join([artist["name"] for artist in track["artists"]])
                    tracks.append({
                        "id": track["id"],
                        "name": track["name"],
                        "artists": artists,
                        "duration_ms": track["duration_ms"],
                        "popularity": track.get("popularity", 0),
                        "album": track.get("album", {}),
                        "added_at": item.get("added_at")
                    })

            print(f"  Fetched {len(tracks)} tracks...")

            # Check if there are more tracks
            if data['next']:
                offset += limit
            else:
                break

        print(f"✅ Found {len(tracks)} liked songs")
        return tracks
