"""Human-readable run reports and derived manifest metadata."""
import json
from pathlib import Path
from windows_client.scan import load_manifest, write_json
from preview3d.quad_splitter import json_digest


def publish(scan, workspace, data):
    report=workspace/'reports/run_report.md'
    data['report_path']=str(report)
    write_json(workspace/'reports/run_report.json',data)
    text=[f"# Full photogrammetry: {data['scan_id']}",f"Status: **{data['colmap_status']}**",f"Workspace: {workspace}",
          f"Combined frames: {data['combined_count']}; split images: {data['split_count']}",
          f"Actual combined dimensions: {data['actual_combined_dimensions']}; split: {data['actual_split_dimensions']}",
          '## Capture settings','```json',json.dumps(data['capture_settings'],indent=2),'```',
          '## Masks',f"Generated: {data['mask_generation_applied']}; settings: {data['mask_settings']}",
          f"Coverage percentages: {data['mask_coverage_stats']}",
          'Inspect reports/mask_contact_sheet.jpg and reports/mask_inspection/. White includes pixels; black excludes them.',
          '## Sharpness',f"Per-camera averages: {data['sharpness_stats']['per_camera_average']}",
          'Per-image scores: reports/sharpness_report.csv. The threshold is heuristic, not a focus guarantee.',
          '## Reconstruction',f"COLMAP detected: {data['colmap_detected']}; version: {data['colmap_version']}",
          f"COLMAP output: {data.get('colmap_output_path')}", 'Commands actually run:', '```json',json.dumps(data['colmap_commands'],indent=2),'```',
          f"RealityCapture/RealityScan: {data['realitycapture_export_path']}",'## Warnings and errors']
    text += ['- '+w for w in data['warnings']+data['errors']] or ['None recorded.']
    text += ['## Next action',data['next_action'],'Original images are preserved. No quick-preview mesh is presented as photogrammetry.']
    report.write_text('\n\n'.join(text)+'\n',encoding='utf-8')
    manifest=load_manifest(scan)
    # Keep an immutable pre-full manifest so verified downloads can still resume.
    backup=scan/'manifest.pre_full.json'
    if not backup.exists(): write_json(backup,manifest)
    for key in ('full_photogrammetry_mode','capture_width','capture_height','actual_combined_dimensions','actual_split_dimensions',
                'steps','rotation_seconds','estimated_angles','mask_generation_applied','mask_settings','mask_coverage_stats',
                'sharpness_stats','colmap_detected','colmap_version','colmap_status','colmap_commands','realitycapture_export_path',
                'full_workspace_path','report_path','warnings','errors'):
        manifest[key]=data.get(key)
    manifest['retain']=True
    write_json(scan/'manifest.json',manifest)
    receipt=scan/'quad_split_receipt.json'
    if receipt.exists():
        saved=json.loads(receipt.read_text());saved['manifest_hash']=json_digest(manifest);write_json(receipt,saved)
    write_json(scan/'full_receipt.json',dict(source_hash=json_digest(json.loads(backup.read_text())),manifest_hash=json_digest(manifest)))
    return data


def update_reconstruction(workspace,result):
    workspace=Path(workspace).resolve()
    data=json.loads((workspace/'reports/run_report.json').read_text())
    data.update(colmap_detected=result['detected'],colmap_version=result['version'],colmap_status=result['status'],
                colmap_commands=result['commands'],colmap_output_path=result['output_path'],next_action=result['next_action'])
    data['warnings']+=result['warnings'];data['errors']+=result['errors']
    metadata=json.loads((workspace/'workspace.json').read_text())
    return publish(Path(metadata['scan_path']),workspace,data)
