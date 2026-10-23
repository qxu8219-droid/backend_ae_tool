"""Current P0 runtime/asset contract; independent of Isaac imports."""
import json
from pathlib import Path


def load_profile(project_root: Path) -> dict:
    profile = json.loads((project_root / 'configs/robot-asset.json').read_text())
    profile['asset_path'] = project_root / profile['asset_directory']
    return profile


def physics_launch_args() -> list[str]:
    return ['--/exts/isaacsim.physics.newton/auto_switch_on_startup=false',
            '--/exts/isaacsim.core.simulation_manager/default_engine=physx',
            '--/app/settings/persistent=false']


def check_asset_version(asset_path: Path, expected_version: str) -> None:
    metadata = json.loads((asset_path / 'conversion.json').read_text())
    version = metadata.get('isaac_version')
    if not isinstance(version, list) or not version or version[0] != expected_version:
        raise ValueError(f'Asset must be exported for Isaac Sim {expected_version}; got {version!r}')


def check_runtime(actual_version: str, actual_engine: str, expected_version: str) -> None:
    if actual_version != expected_version:
        raise ValueError(f'Expected Isaac Sim {expected_version}, got {actual_version}')
    if actual_engine.lower() != 'physx':
        raise ValueError(f'Robot asset requires PhysX, got {actual_engine}')
