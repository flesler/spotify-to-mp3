"""
Spotify API client for accessing public playlist data.
Uses client credentials flow (requires Spotify Premium on app owner account).
"""

import base64

import requests


class API:
    """Spotify API client using client credentials flow"""

    def __init__(self, client_id, client_secret):
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = None
        self._get_access_token()

    def _get_access_token(self):
        """Get Spotify access token using client credentials flow"""
        url = "https://accounts.spotify.com/api/token"

        # Encode client credentials
        credentials = f"{self.client_id}:{self.client_secret}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()

        headers = {"Authorization": f"Basic {encoded_credentials}", "Content-Type": "application/x-www-form-urlencoded"}

        data = {"grant_type": "client_credentials"}

        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()

        token_data = response.json()
        self.access_token = token_data["access_token"]

    def _make_request(self, url):
        """Make authenticated request to Spotify API"""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    def extract_playlist_id(self, playlist_input):
        """Extract playlist ID from URL or return as-is if already an ID"""
        if playlist_input.startswith("http"):
            # Extract from URL
            if "playlist/" in playlist_input:
                return playlist_input.split("playlist/")[1].split("?")[0]
            else:
                from urllib.parse import urlparse

                parsed = urlparse(playlist_input)
                path_parts = parsed.path.split("/")
                if "playlist" in path_parts:
                    idx = path_parts.index("playlist")
                    if idx + 1 < len(path_parts):
                        return path_parts[idx + 1]
        return playlist_input

    def get_playlist_tracks(self, playlist_id):
        """Get all tracks from a Spotify playlist"""
        playlist_id = self.extract_playlist_id(playlist_id)

        # Get playlist info
        playlist_url = f"https://api.spotify.com/v1/playlists/{playlist_id}"
        playlist_data = self._make_request(playlist_url)
        playlist_name = playlist_data["name"]

        print(f"📋 Playlist: {playlist_name}")
        print(f"🔗 ID: {playlist_id}")

        # Get tracks with pagination
        tracks = []
        url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"

        while url:
            data = self._make_request(url)

            for item in data["items"]:
                if item["track"] and item["track"]["type"] == "track":
                    track = item["track"]
                    artists = ", ".join([artist["name"] for artist in track["artists"]])
                    tracks.append(
                        {
                            "id": track["id"],
                            "name": track["name"],
                            "artists": artists,
                            "duration_ms": track["duration_ms"],
                            "popularity": track["popularity"],
                            "album": track.get("album", {}),  # Include full album data
                        }
                    )

            url = data.get("next")

        print(f"✅ Found {len(tracks)} tracks")
        return tracks, playlist_name
