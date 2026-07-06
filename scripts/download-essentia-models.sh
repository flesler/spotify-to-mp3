#!/bin/bash
# Download Essentia TensorFlow models (host only).
# Uses classification-heads on MusiCNN embeddings (see docs/essentia.md).
# Legacy fat classifiers/ in models/essentia/ are left on disk if already present.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_DIR="$ROOT_DIR/models/essentia"
BASE="https://essentia.upf.edu/models"
mkdir -p "$MODELS_DIR"

FAILED=0

download() {
    local path="$1"
    local out="$MODELS_DIR/$(basename "$path")"
    if [ -f "$out" ] && [ -s "$out" ]; then
        echo "  have $(basename "$path")"
        return 0
    fi
    rm -f "$out"
    echo "  ↓ $(basename "$path")"
    if curl -fL --retry 5 --retry-delay 3 --connect-timeout 30 --max-time 120 -o "$out" "$BASE/$path"; then
        if [ -s "$out" ]; then
            return 0
        fi
        echo "  ✗ empty $(basename "$path")"
        rm -f "$out"
    else
        echo "  ✗ failed $(basename "$path")"
        rm -f "$out"
    fi
    FAILED=$((FAILED + 1))
    return 1
}

HEADS=(
    mood_happy mood_electronic mood_relaxed mood_aggressive mood_sad mood_party mood_acoustic
    gender danceability voice_instrumental tonal_atonal
    genre_dortmund genre_rosamerica genre_tzanetakis
)

REQUIRED=(
    msd-musicnn-1.pb
    msd-musicnn-1.json
    deam-msd-musicnn-2.pb
)

for head in "${HEADS[@]}"; do
    REQUIRED+=("${head}-msd-musicnn-1.pb" "${head}-msd-musicnn-1.json")
done

echo "📥 Essentia models → $MODELS_DIR"
download "autotagging/msd/msd-musicnn-1.pb"
download "autotagging/msd/msd-musicnn-1.json"
download "classification-heads/deam/deam-msd-musicnn-2.pb"

for head in "${HEADS[@]}"; do
    download "classification-heads/${head}/${head}-msd-musicnn-1.pb"
    download "classification-heads/${head}/${head}-msd-musicnn-1.json"
done

MISSING=0
for f in "${REQUIRED[@]}"; do
    if [ ! -s "$MODELS_DIR/$f" ]; then
        echo "  ✗ missing $f"
        MISSING=$((MISSING + 1))
    fi
done

PB_COUNT="$(find "$MODELS_DIR" -name '*.pb' | wc -l)"
if [ "$MISSING" -eq 0 ] && [ "$FAILED" -eq 0 ]; then
    echo "✅ Done ($PB_COUNT .pb files, all required present)"
    exit 0
fi

echo "⚠️  incomplete: $FAILED download errors, $MISSING missing — re-run this script"
exit 1
