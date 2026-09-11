"""Consistent frame selection for reconstruction and photo inspection."""
import numpy as np


def validate_limit(max_frames):
    if type(max_frames) is not int or not 0 <= max_frames <= 10000:
        raise ValueError("max_frames must be an integer from 0 to 10000 (0 means all images)")
    return max_frames


def select_frames(manifest, max_frames=0):
    validate_limit(max_frames)
    frames = sorted(manifest["frames"], key=lambda f: (f["angle_deg"], f["camera_id"]))
    count = len(frames) if max_frames == 0 else min(max_frames, len(frames))
    return [frames[i] for i in np.linspace(0, len(frames)-1, count, dtype=int)]
