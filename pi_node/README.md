# Pi capture node

Entry points from the repository root:

- Mock API: python -m pi_node.app --host 127.0.0.1 --port 8000 --mock
- Diagnostics: python -m pi_node.camera_backends.rpicam_backend --diagnose
- One image: python -m pi_node.camera_backends.rpicam_backend --test-capture
- Physical API: python -m pi_node.app --host 0.0.0.0 --port 8000 --backend rpicam

camera_backends/base.py describes the image boundary; mock and rpicam adapters live alongside it. config.py validates settings. scan_manager.py owns timing, backgrounds and durable state. service.py and capture.py preserve previous imports. splitter.py rejects unverified quad splitting.

See the [API](../docs/pi_node_api.md), [runbook](../docs/physical_demo_runbook.md), and [Windows setup](../README.md). Configure the Pi OS/vendor camera stack separately. Missing camera tools leave the API reachable with diagnostic errors.
