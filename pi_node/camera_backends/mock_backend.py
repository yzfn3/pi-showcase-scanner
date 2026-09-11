"""Deterministic views of a different synthetic object for each scan."""
import hashlib
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from sample_data.generate import camera_configuration, render, random_object


class MockCapture:
    mock_mode = True
    name = "mock"
    combined_quad_output = False
    size = 320
    label_height = 56

    def cameras(self):
        cameras = camera_configuration()
        for camera in cameras:
            camera["preview_crop"] = [0, 0, self.size, self.size]
        return cameras

    def background(self, camera):
        return Image.new("RGB", (self.size, self.size+self.label_height), (229, 233, 232))

    def capture(self, camera, *, scan_id, step, angle_deg, captured_at):
        background = np.full((self.size, self.size, 3), [229, 233, 232], dtype=np.uint8)
        image = self.background(camera)
        seed = hashlib.sha256(scan_id.encode()).hexdigest()[:32]
        image.paste(render(camera, angle_deg, self.size, background, random_object(seed)), (0, 0))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, self.size, self.size, image.height), fill=(25, 47, 46))
        font = ImageFont.load_default(size=11)
        # Capture labels remain in transferred JPGs; preview_crop excludes the strip.
        lines = [scan_id, f"step {step:03d} | {camera['id']} | angle {angle_deg:.1f} deg", captured_at]
        for row, text in enumerate(lines):
            draw.text((6, self.size+3+row*16), text, fill=(235, 244, 236), font=font)
        return image
