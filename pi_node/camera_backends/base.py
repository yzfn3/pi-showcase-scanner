"""Small image capture interface; scan timing belongs to the manager."""
from typing import Protocol
from PIL import Image


class CaptureBackend(Protocol):
    """Common discovery contract. Scheduling always belongs to the manager."""

    name: str
    mock_mode: bool
    combined_quad_output: bool

    def cameras(self) -> list[dict]: ...


class ImageCaptureBackend(CaptureBackend, Protocol):
    """Synthetic adapter returns PIL images for the manager to encode."""

    def background(self, camera: dict) -> Image.Image: ...

    def capture(self, camera: dict, *, scan_id: str, step: int,
                angle_deg: float, captured_at: str) -> Image.Image: ...


class FileCaptureBackend(CaptureBackend, Protocol):
    """Hardware adapter saves one validated image and returns its command log."""

    def capture_file(self, path, camera: dict, settings: dict) -> dict: ...


