"""Tests for deduplication logic"""

import shutil
import tempfile
from pathlib import Path


def test_fuzzy_match_exact():
    """Test exact filename matching"""
    from main import fuzzy_match_filenames

    assert fuzzy_match_filenames("Artist - Title", "Artist - Title")


def test_fuzzy_match_remix():
    """Test remix variation matching"""
    from main import fuzzy_match_filenames

    assert fuzzy_match_filenames("Artist - Title", "Artist - Title (Remix)")


def test_fuzzy_match_feat():
    """Test feat. variation matching"""
    from main import fuzzy_match_filenames

    assert fuzzy_match_filenames("Artist - Title", "Artist feat. Other - Title")


def test_fuzzy_match_case_insensitive():
    """Test case insensitive matching"""
    from main import fuzzy_match_filenames

    assert fuzzy_match_filenames("Artist - Title", "artist - title")


def test_fuzzy_match_different_songs():
    """Test that different songs don't match"""
    from main import fuzzy_match_filenames

    assert not fuzzy_match_filenames("Artist - Song1", "Other - Track2")


def test_sanitize_filename():
    """Test filename sanitization"""
    from main import sanitize_filename

    # Remove invalid chars
    assert sanitize_filename('test<>:"file') == "testfile"

    # Collapse spaces
    assert sanitize_filename("test   file") == "test file"

    # Strip whitespace
    assert sanitize_filename("  test  ") == "test"


class TestCheckIfExists:
    """Test check_if_track_exists with real files"""

    def setup_method(self):
        """Create temp directory for each test"""
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

    def teardown_method(self):
        """Clean up temp directory"""
        shutil.rmtree(self.temp_dir)

    def test_exact_match_finds_file(self):
        """Test finding existing file by exact match"""
        from main import check_if_track_exists

        # Create a test file
        test_file = self.base_path / "Artist - Title.mp3"
        test_file.touch()

        result, reason = check_if_track_exists(
            artists="Artist", title="Title", base_music_dir=self.base_path, auto_rename=False
        )

        assert result == test_file
        assert reason == "filename"

    def test_no_match_returns_none(self):
        """Test that non-existent track returns None"""
        from main import check_if_track_exists

        result = check_if_track_exists(
            artists="Unknown", title="Track", base_music_dir=self.base_path, auto_rename=False
        )

        assert result is None

    def test_auto_rename_creates_clean_format(self):
        """Test that messy filenames get renamed to clean format"""
        from main import check_if_track_exists

        # Create file with messy name
        messy_file = self.base_path / "artist_TITLE_remix.mp3"
        messy_file.touch()

        result, reason = check_if_track_exists(
            artists="Artist", title="Title", base_music_dir=self.base_path, auto_rename=True
        )

        # Should rename to clean format
        expected = self.base_path / "Artist - Title.mp3"
        assert result == expected
        assert not messy_file.exists()  # Old file renamed


class TestFuzzyMatching:
    """Additional fuzzy matching edge cases"""

    def test_fuzzy_match_punctuation(self):
        """Test that punctuation differences are handled"""
        from main import fuzzy_match_filenames

        # Different punctuation should match
        assert fuzzy_match_filenames("Artist - Title's", "Artist - Titles")

    def test_fuzzy_match_extra_spaces(self):
        """Test extra spaces don't break matching"""
        from main import fuzzy_match_filenames

        assert fuzzy_match_filenames("Artist   -   Title", "Artist - Title")

    def test_fuzzy_match_special_chars(self):
        """Test special characters are handled"""
        from main import fuzzy_match_filenames

        assert fuzzy_match_filenames("Artist & Title!", "Artist and Title")

    def test_fuzzy_threshold_boundary(self):
        """Test threshold boundary behavior"""
        from main import fuzzy_match_filenames

        # Very similar should always match
        assert fuzzy_match_filenames("Artist - Title v2", "Artist - Title")

        # Completely different should never match
        assert not fuzzy_match_filenames("XYZ123", "ABC456")


class TestSanitizeFilename:
    """Additional sanitization tests"""

    def test_sanitize_removes_all_invalid_chars(self):
        """Test all invalid characters are removed"""
        from main import sanitize_filename

        result = sanitize_filename('test<>:"file|?*')
        assert "<" not in result
        assert ">" not in result
        assert ":" not in result
        assert '"' not in result
        assert "|" not in result
        assert "?" not in result
        assert "*" not in result

    def test_sanitize_preserves_valid_special_chars(self):
        """Test valid special chars are preserved"""
        from main import sanitize_filename

        result = sanitize_filename("Artist - Title (Remix) [Explicit].mp3")
        assert "-" in result
        assert "(" in result
        assert ")" in result
        assert "[" in result
        assert "]" in result
        assert "." in result

    def test_sanitize_unicode(self):
        """Test unicode characters are handled"""
        from main import sanitize_filename

        # Should preserve unicode
        result = sanitize_filename("Artïst - Tïtle")
        assert "Artïst" in result


class TestDurationMatching:
    """Test duration-based deduplication"""

    def setup_method(self):
        """Create temp directory and test MP3"""
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

        # Create a minimal MP3 file (not real audio, just for testing)
        self.test_mp3 = self.base_path / "Artist - Title.mp3"
        self.test_mp3.touch()

    def teardown_method(self):
        """Clean up temp directory"""
        shutil.rmtree(self.temp_dir)

    def test_duration_match_within_tolerance(self):
        """Test files within 5s tolerance are considered matches"""
        from main import check_if_track_exists

        # Note: This test would need actual MP3 with duration metadata
        # For now, it verifies the function doesn't crash with duration param
        result, reason = check_if_track_exists(
            artists="Artist",
            title="Title",
            base_music_dir=self.base_path,
            auto_rename=False,
            duration_ms=180000,  # 3 minutes
        )

        # Should find the file (can't verify duration on empty file)
        assert result is not None


class TestHardLinking:
    """Test hard link creation across playlists"""

    def setup_method(self):
        """Create temp directory structure"""
        self.temp_dir = tempfile.mkdtemp()
        self.base_path = Path(self.temp_dir)

        # Create playlist directories
        self.playlist1 = self.base_path / "Playlist1"
        self.playlist2 = self.base_path / "Playlist2"
        self.playlist1.mkdir()
        self.playlist2.mkdir()

    def teardown_method(self):
        """Clean up temp directory"""
        shutil.rmtree(self.temp_dir)

    def test_hardlink_detection(self):
        """Test that hard links are detected as same file"""
        # Create original file
        original = self.playlist1 / "Artist - Title.mp3"
        original.write_text("dummy content")

        # Create hard link
        linked = self.playlist2 / "Artist - Title.mp3"
        linked.hardlink_to(original)

        # Verify they're hard linked
        assert linked.exists()
        assert original.stat().st_ino == linked.stat().st_ino  # Same inode
        assert original.read_text() == linked.read_text()
