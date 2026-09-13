import unittest
from PIL import Image
import numpy as np
from photogrammetry.vggt_runner import brighten
from photogrammetry.vggt_worker import choose_frames

class VGGTTests(unittest.TestCase):
    def test_brightness_preserves_endpoints_and_original(self):
        image=Image.fromarray(np.array([[[0,64,255]]],dtype=np.uint8))
        adjusted=np.asarray(brighten(image,.8))[0,0]
        self.assertEqual(adjusted[0],0);self.assertEqual(adjusted[2],255)
        self.assertGreater(adjusted[1],64)
        self.assertEqual(np.asarray(image)[0,0,1],64)
        with self.assertRaises(ValueError):brighten(image,float('nan'))

    def test_sampling_preserves_all_physical_cameras(self):
        frames=[dict(step=s,camera_id=f'cam_{c:02d}',name=f'{s}_{c}') for s in range(16) for c in range(1,5)]
        selected=choose_frames(frames,32)
        self.assertEqual(len(selected),32)
        for c in range(1,5):
            group=[f for f in selected if f['camera_id']==f'cam_{c:02d}']
            self.assertEqual(len(group),8)
            self.assertEqual([group[0]['step'],group[-1]['step']],[0,15])

if __name__=='__main__':unittest.main()
