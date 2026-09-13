import json
from pathlib import Path

class Worker:
    def __init__(self, command, **kwargs):
        workspace=Path(command[-1]); config=json.loads((workspace/'request.json').read_text())
        result=dict(engine='vggt',status='vggt_complete',processed_images=len(config['frames']),
                    input_images=config['input_images'],mesh_status='not run',errors=[],warnings=[])
        (workspace/'reports/vggt.json').write_text(json.dumps(result))
        self.stdout=iter([])
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def wait(self):return 0
