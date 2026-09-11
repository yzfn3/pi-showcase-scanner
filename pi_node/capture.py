"""Compatibility imports for existing mock integrations."""
from pi_node.camera_backends.base import CaptureBackend
from pi_node.camera_backends.mock_backend import MockCapture

__all__ = ["CaptureBackend", "MockCapture"]
