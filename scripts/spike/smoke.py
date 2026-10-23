"""P0-only Isaac Sim 6.2 / PhysX probe; no production replay engine.

Run through scripts/run_p0.sh with the selected pip Python. CPU tests do not import Isaac Sim.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import signal
import sys
import time
import uuid
import zlib
from pathlib import Path


def sample_pose(log_time_s: float, joint_names: list[str]) -> list[float]:
    if not math.isfinite(log_time_s):
        raise ValueError('log time must be finite')
    wave = math.sin(log_time_s * math.pi / 3)
    values = {}
    for i in range(1, 4):
        values[f'steer_motor_joint{i}'] = 0.3 * wave
        values[f'wheel_motor_joint{i}'] = log_time_s
    values.update(torso_joint1=0.15 * wave, torso_joint2=-0.25,
                  torso_joint3=0.2, torso_joint4=0.3 * wave)
    for side, sign in [('left', 1), ('right', -1)]:
        for i in range(1, 8):
            values[f'{side}_arm_joint{i}'] = 0.0
        values[f'{side}_arm_joint1'] = 0.2 * wave
        values[f'{side}_arm_joint2'] = sign * (0.35 + 0.2 * wave)
        values[f'{side}_arm_joint4'] = -0.5 + 0.15 * wave
        values[f'{side}_gripper_finger_joint1'] = 0.025 + 0.015 * wave
        values[f'{side}_gripper_finger_joint2'] = -0.025 - 0.015 * wave
    if any(name not in values for name in joint_names):
        raise ValueError('unknown joint name')
    return [values[name] for name in joint_names]


def decode_marker_rgb(samples) -> int:
    value = 0
    for bit, pixel in enumerate(samples):
        r, g = float(pixel[0]), float(pixel[1])
        if abs(g - r) < 30:
            raise ValueError('render marker is not distinguishable')
        value |= int(g > r) << bit
    return value


def capture_stamp(capture_id: int) -> int:
    if type(capture_id) is not int or not 0 < capture_id < 2**32:
        raise ValueError('capture ID must be a positive uint32 without wrap')
    return capture_id | (zlib.crc32(capture_id.to_bytes(4, 'big')) << 32)


def external_dependencies(asset_root, paths, builtin_material) -> list[str]:
    root = Path(asset_root).resolve()
    builtin = Path(builtin_material).resolve() if builtin_material is not None else None
    return [str(p) for p in paths if not Path(str(p)).resolve().is_relative_to(root)
            and Path(str(p)).resolve() != builtin]


def runtime_pose(values, names) -> list[float]:
    """Visual wheel pose is periodic; retain source winding separately."""
    return [(value + math.pi) % (2 * math.pi) - math.pi
            if name.startswith('wheel_motor_joint') else value
            for value, name in zip(values, names)]


def prepare_output(path: Path) -> None:
    if path.exists() and any(path.iterdir()):
        raise ValueError('Output directory must be empty; use a new directory for each run')
    path.mkdir(parents=True, exist_ok=True)


def quaternion_angle_error(actual, expected) -> float:
    an = math.sqrt(sum(float(x)**2 for x in actual))
    en = math.sqrt(sum(float(x)**2 for x in expected))
    if not an or not en:
        raise ValueError('zero quaternion')
    dot = abs(sum(float(a)*float(b) for a, b in zip(actual, expected))/(an*en))
    return 2 * math.acos(min(1, dot))


def loop_statistics(mode, iterations, elapsed, durations, jpeg_sizes) -> dict:
    rate = iterations / elapsed if elapsed > 0 else 0
    duration_ms = 1000 * sum(durations) / len(durations) if durations else None
    if mode in ('rtsp', 'rtsp_sync'):
        return {'render_iterations': iterations, 'server_loop_fps': rate,
                'mean_render_iteration_ms': duration_ms}
    return {'frames': iterations, 'published_fps': rate, 'mean_capture_ms': duration_ms,
            'mean_jpeg_bytes': sum(jpeg_sizes) / len(jpeg_sizes) if jpeg_sizes else None}


def snapshot_transport_error(stats):
    if stats.get('stream_failed') or stats.get('stream_errors', 0):
        return 'Snapshot RTSP transport failed; inspect snapshot_writer diagnostics'
    if stats.get('prepared_frames', 0) == 0:
        return 'No encoded output carried a raw-validated snapshot catalog'
    return None


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(Path(__file__).parent))
    from runtime_profile import load_profile, physics_launch_args, check_runtime, check_asset_version
    profile = load_profile(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--asset-dir', type=Path, default=profile['asset_path'])
    parser.add_argument('--output-dir', type=Path, default=root / 'outputs/p0/live')
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--fps', type=float, default=30)
    parser.add_argument('--width', type=int, default=960)
    parser.add_argument('--height', type=int, default=720)
    parser.add_argument('--mode', choices=['baseline', 'paired', 'rtsp', 'rtsp_sync'], default='baseline')
    parser.add_argument('--rtsp-port', type=int, default=8554)
    parser.add_argument('--renderer', choices=['RaytracedLighting', 'PathTracing'], default='RaytracedLighting')
    parser.add_argument('--diagnose-capture', action='store_true')
    parser.add_argument('--snapshot-debug', action='store_true')
    parser.add_argument('--start-paused', action='store_true',
                        help='Render the initial ready pose at log time zero until play is requested')
    parser.add_argument('--rt-subframes', type=int, default=8)
    parser.add_argument('--samples', type=int, default=8)
    args = parser.parse_args()
    if args.seconds <= 0 or not 1 <= args.fps <= 60 or args.rt_subframes < 1:
        parser.error('seconds must be positive and fps in [1,60]')
    prepare_output(args.output_dir)
    sys.path.insert(0, str(Path(__file__).parent))
    from asset_audit import CANONICAL_JOINTS, audit_asset
    from paired_preview import publish_frame, _atomic_write
    audit = audit_asset(args.asset_dir)
    if not audit['passed']:
        raise RuntimeError(f'Asset audit failed: {audit["errors"]}')
    check_asset_version(args.asset_dir, profile['isaacsim_version'])
    started = time.monotonic()
    from isaacsim import SimulationApp
    app = SimulationApp({'headless': True, 'width': args.width, 'height': args.height,
                         'window_width': args.width, 'window_height': args.height,
                         'renderer': args.renderer, 'limit_cpu_threads': 12,
                         'disable_viewport_updates': args.mode == 'rtsp_sync',
                         'hide_ui': True, 'anti_aliasing': 0,
                         'samples_per_pixel_per_frame': args.samples, 'denoiser': False,
                         'extra_args': physics_launch_args()},
                        experience=str(Path(os.environ['EXP_PATH']) / 'isaacsim.exp.base.zero_delay.kit'))
    report = {'status': 'running', 'asset_dir': str(args.asset_dir.resolve()),
              'scope': args.mode, 'transport': 'paired-http-jpeg-experiment' if args.mode == 'paired' else None,
              'checks': [], 'marker_mismatches': 0 if args.mode == 'paired' else None,
              'marker_validation_performed': args.mode == 'paired', 'browser_verified': False,
              'settings': {'renderer': args.renderer, 'width': args.width, 'height': args.height,
                  'target_fps': args.fps, 'samples': args.samples, 'rt_subframes': args.rt_subframes,
                  'seconds': args.seconds, 'rtsp_port': args.rtsp_port},
              'python_version': sys.version, 'python_executable': sys.executable}
    running = True
    snapshot_writer = None
    session_id = uuid.uuid4().hex
    report['session_id'] = session_id

    def terminate(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        import numpy as np
        import carb
        from PIL import Image, ImageDraw
        from pxr import Gf, UsdGeom, UsdLux, UsdPhysics, UsdShade, PhysicsSchemaTools, UsdUtils
        import omni.usd
        import omni.replicator.core as rep
        from isaacsim.core.api import World
        from isaacsim.core.prims import SingleArticulation
        from isaacsim.core.utils.viewports import set_camera_view
        from isaacsim.core.version import get_version
        from isaacsim.core.simulation_manager import SimulationManager
        report['isaacsim_version'] = list(get_version())
        report['physics_backend'] = SimulationManager.get_active_physics_engine()
        check_runtime(report['isaacsim_version'][0], report['physics_backend'], profile['isaacsim_version'])
        report['renderer_settings'] = {key: carb.settings.get_settings().get(key) for key in
            ['/rtx/rendermode', '/rtx/post/aa/op', '/app/hydraEngine/waitIdle',
             '/app/updateOrder/checkForHydraRenderComplete']}
        if args.mode == 'rtsp':
            from isaacsim.core.utils.extensions import enable_extension
            stream_path = '/exts/omni.kit.livestream.app/primaryStream/'
            settings = carb.settings.get_settings()
            for key, value in {'streamType': 'rtsp', 'streamPort': args.rtsp_port,
                               'targetFps': int(args.fps), 'allowDynamicResize': False}.items():
                settings.set(stream_path + key, value)
            enable_extension('omni.kit.livestream.rtsp')
            enable_extension('omni.kit.livestream.app')
            report['transport'] = 'rtsp'
            report['rtsp_url'] = f'rtsp://127.0.0.1:{args.rtsp_port}/stream'
        world = World(stage_units_in_meters=1, physics_dt=1/60,
                      rendering_dt=1/args.fps, backend='numpy')
        stage = omni.usd.get_context().get_stage()
        stage.DefinePrim('/World/Robot', 'Xform').GetReferences().AddReference(
            str((args.asset_dir / 'r1pro_2026.usd').resolve()))
        PhysicsSchemaTools.addGroundPlane(stage, '/World/Ground', 'Z', 20,
                                          Gf.Vec3f(0), Gf.Vec3f(.35))
        UsdLux.DomeLight.Define(stage, '/World/Light').CreateIntensityAttr(800)
        sun = UsdLux.DistantLight.Define(stage, '/World/Sun')
        sun.CreateIntensityAttr(1800)
        UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-35, 25, 25))
        roots = [p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
        if len(roots) != 1:
            raise RuntimeError(f'Expected one articulation root, got {len(roots)}')
        report['articulation_root'] = str(roots[0].GetPath())
        if report['articulation_root'] != profile['referenced_runtime_root']:
            raise RuntimeError('Asset articulation root does not match the selected runtime profile')
        robot = world.scene.add(SingleArticulation(report['articulation_root'], name='robot'))
        world.reset()
        report['physics_backend'] = SimulationManager.get_active_physics_engine()
        check_runtime(report['isaacsim_version'][0], report['physics_backend'], profile['isaacsim_version'])
        world.step(render=False)
        actual_names = list(robot.dof_names)
        if set(actual_names) != set(CANONICAL_JOINTS) or len(actual_names) != 28:
            raise RuntimeError(f'Unexpected DOF names: {actual_names}')
        indices = np.array([actual_names.index(n) for n in CANONICAL_JOINTS])
        report['dof_names'] = actual_names
        report['canonical_order'] = list(CANONICAL_JOINTS)
        report['usd_layers'] = [layer.realPath for layer in stage.GetUsedLayers() if layer.realPath]
        dep_layers, dep_assets, unresolved = UsdUtils.ComputeAllDependencies(
            str((args.asset_dir / 'r1pro_2026.usd').resolve()))
        asset_root = args.asset_dir.resolve()
        builtin_relative = profile.get('runtime_builtin_dependency')
        builtin_material = Path(os.environ['EXP_PATH']).parent / builtin_relative if builtin_relative else None
        external = external_dependencies(asset_root,
            [layer.realPath for layer in dep_layers] + list(dep_assets), builtin_material)
        unresolved_required = [str(p) for p in unresolved
                               if not builtin_material or str(p) != builtin_material.name]
        report['asset_dependencies'] = {'layers': len(dep_layers), 'assets': len(dep_assets),
            'external_files': external, 'unresolved': [str(p) for p in unresolved],
            'builtin_materials_allowed': [builtin_material.name] if builtin_material else []}
        if external or unresolved_required:
            raise RuntimeError('Asset dependency closure failed')
        camera = UsdGeom.Camera.Define(stage, '/World/Camera')
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.01, 100))
        camera.CreateFocalLengthAttr(35)
        camera.CreateHorizontalApertureAttr(36)
        camera.CreateVerticalApertureAttr(36 * args.height / args.width)
        set_camera_view(eye=np.array([3.1, 2.7, 2.1]), target=np.array([0, 0, 0.85]),
                        camera_prim_path='/World/Camera')
        if args.mode == 'rtsp':
            from omni.kit.viewport.utility import get_active_viewport
            viewport = get_active_viewport()
            if viewport:
                viewport.set_active_camera('/World/Camera')
        product = rep.create.render_product('/World/Camera', (args.width, args.height),
                                            force_new=args.mode == 'rtsp_sync')
        if args.mode == 'rtsp_sync':
            from omni.kit.viewport.utility import get_active_viewport
            viewport = get_active_viewport()
            if viewport and str(viewport.render_product_path) == str(product.path):
                raise RuntimeError('Snapshot camera must use a separate render product from disabled viewport')
            report['capture_rendering'] = {
                'camera_render_product': str(product.path),
                'viewport_render_product': str(viewport.render_product_path) if viewport else None,
                'viewport_updates_enabled': viewport.updates_enabled if viewport else None,
                'dlss_history_reset_per_capture': True}
        rgb = None
        if args.mode != 'rtsp_sync':
            rgb = rep.AnnotatorRegistry.get_annotator('rgb')
            rgb.attach([product])

        # Actual scene geometry encodes render_seq; read pixels independently of metadata.
        marker_switches = []
        sample_pixels = []
        marker_bits = 64 if args.mode == 'rtsp_sync' else 12
        for bit in range(marker_bits):
            if marker_bits == 64:
                x = -.475 + (bit % 32)*.03
                y = (.32-(bit//32)*.04)*args.height/args.width*4/3
                size = .022
            else:
                x,y,size = -.44+bit*.055, .32*args.height/args.width*4/3, .035
            z = -1.0
            cube = UsdGeom.Cube.Define(stage, f'/World/Camera/FrameBit{bit}')
            cube.CreateSizeAttr(1)
            xf = UsdGeom.Xformable(cube)
            xf.AddTranslateOp().Set(Gf.Vec3d(x, y, z))
            xf.AddScaleOp().Set(Gf.Vec3f(size, size, .002))
            mat = UsdShade.Material.Define(stage, f'/World/MarkerMaterials/Bit{bit}')
            shader = UsdShade.Shader.Define(stage, f'/World/MarkerMaterials/Bit{bit}/Shader')
            shader.CreateIdAttr('UsdPreviewSurface')
            from pxr import Sdf
            shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0))
            shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(1, 0, 0))
            mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
            UsdShade.MaterialBindingAPI.Apply(cube.GetPrim()).Bind(mat)
            # Fixed materials avoid renderer temporal blending of animated shader inputs.
            green = UsdGeom.Cube.Define(stage, f'/World/Camera/FrameGreen{bit}')
            green.CreateSizeAttr(1)
            green_xf = UsdGeom.Xformable(green)
            green_xf.AddTranslateOp().Set(Gf.Vec3d(x, y, z + .005))
            green_xf.AddScaleOp().Set(Gf.Vec3f(size, size, .002))
            green_mat = UsdShade.Material.Define(stage, f'/World/MarkerMaterials/Green{bit}')
            green_shader = UsdShade.Shader.Define(stage, f'/World/MarkerMaterials/Green{bit}/Shader')
            green_shader.CreateIdAttr('UsdPreviewSurface')
            green_shader.CreateInput('emissiveColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0))
            green_shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0, 1, 0))
            green_mat.CreateSurfaceOutput().ConnectToSource(green_shader.ConnectableAPI(), 'surface')
            UsdShade.MaterialBindingAPI.Apply(green.GetPrim()).Bind(green_mat)
            marker_switches.append(green.CreateVisibilityAttr('inherited'))
            sample_pixels.append([round((x / (18/35) + 1) * args.width/2),
                                  round((1 - y/(18/35 * args.height/args.width)) * args.height/2)])

        def apply(q, xyz=(0.0, 0.0, 0.015), yaw=0.0):
            robot.set_world_pose(np.array(xyz), np.array([math.cos(yaw/2), 0, 0, math.sin(yaw/2)]))
            robot.set_joint_positions(np.array(runtime_pose(q, CANONICAL_JOINTS)), joint_indices=indices)
            robot.set_joint_velocities(np.zeros(28))
            robot.set_linear_velocity(np.zeros(3))
            robot.set_angular_velocity(np.zeros(3))

        zero = np.zeros(28)
        for label, overrides, xyz, yaw in [
            ('zero', {}, (0, 0, .015), 0),
            ('steering_wheels', {n: .4 for n in CANONICAL_JOINTS[:6]}, (0, 0, .015), 0),
            ('torso', {n: .2 for n in CANONICAL_JOINTS if n.startswith('torso')}, (0, 0, .015), 0),
            ('arms_grippers', dict(zip(CANONICAL_JOINTS, sample_pose(1, list(CANONICAL_JOINTS)))), (0, 0, .015), 0),
            ('chassis', {}, (.4, -.2, .015), .7),
        ]:
            q = np.array([overrides.get(n, 0) for n in CANONICAL_JOINTS])
            apply(q, xyz, yaw)
            for _ in range(4):
                world.render()
            read = robot.get_joint_positions(joint_indices=indices)
            pos, quat = robot.get_world_pose()
            error = float(np.max(np.abs(read-q)))
            base_error = float(np.max(np.abs(pos-np.array(xyz))))
            angle_error = quaternion_angle_error(quat, [math.cos(yaw/2),0,0,math.sin(yaw/2)])
            check = {'name': label, 'joint_max_error': error, 'base_max_error': base_error,
                     'base_angle_error_rad': angle_error,
                     'passed': error < 1e-5 and base_error < 1e-5 and angle_error < 1e-5}
            report['checks'].append(check)
            print('POSE_CHECK', json.dumps(check), flush=True)
        if not all(item['passed'] for item in report['checks']):
            raise RuntimeError('Kinematic pose or pause-hold readback failed')
        # Audit out-of-limit behavior without advancing physics or sending anything to hardware.
        common = json.loads((args.asset_dir / 'reproduction/configs/r1pro_2026_common.json').read_text())
        q = zero.copy()
        q[list(CANONICAL_JOINTS).index('torso_joint4')] = common['joints']['torso_joint4']['limit']['upper'] + .1
        apply(q)
        world.render()
        report['over_limit_probe'] = {'requested': float(q[9]),
                                      'readback': float(robot.get_joint_positions(joint_indices=indices)[9]),
                                      'physics_advanced': False}
        apply(sample_pose(0, list(CANONICAL_JOINTS)))
        for _ in range(4):
            world.render()
        for visible in marker_switches:
            visible.Set('invisible')
        for _ in range(10):
            world.render()
        if args.mode == 'rtsp_sync':
            from rtsp_snapshot_writer import create_snapshot_writer
            snapshot_writer = create_snapshot_writer(stage, product, '/World/Camera',
                port=args.rtsp_port, width=args.width, height=args.height,
                debug_dir=args.output_dir/'capture-debug' if args.snapshot_debug else None)
            report['transport'] = 'rtsp-h264-sei-catalog-pixel-identity-experiment'
            report['rtsp_url'] = f'rtsp://127.0.0.1:{args.rtsp_port}/stream'
        if args.mode == 'baseline':
            q = sample_pose(1, list(CANONICAL_JOINTS))
            apply(q)
            world.step(render=False)
            apply(q)
            world.render()
            rep.orchestrator.step(delta_time=0.0, rt_subframes=8, pause_timeline=False)
            frame = rgb.get_data()
            if not isinstance(frame, np.ndarray) or frame.size == 0:
                raise RuntimeError('Baseline camera returned no image')
            Image.fromarray(frame).save(args.output_dir / 'baseline.png')
            report.update(status='pass', startup_seconds=time.monotonic()-started,
                          image_size=list(frame.shape), image_std=float(frame[:,:,:3].std()))
            return 0
        report['startup_seconds'] = time.monotonic() - started
        state, speed, log_time, epoch, seq, request_id = ('ready' if args.start_paused else 'playing'), 1.0, 0.0, 0, 0, 0
        loop_start = last_tick = time.monotonic()
        capture_times, jpeg_sizes = [], []
        print('P0_READY', str(args.output_dir), flush=True)
        (args.output_dir / 'ready.json').write_text(json.dumps(report, indent=2))
        with (args.output_dir / 'trace.jsonl').open('w') as trace:
            while running and time.monotonic() - loop_start < args.seconds:
                begin = time.monotonic()
                dt = begin - last_tick
                last_tick = begin
                if state == 'playing':
                    log_time = min(12.0, log_time + dt * speed)
                    if log_time >= 12:
                        state = 'ended'
                command_path = args.output_dir / 'control.json'
                if command_path.exists():
                    command = json.loads(command_path.read_text())
                    if command['request_id'] > request_id:
                        request_id = command['request_id']
                        action = command['action']
                        if action == 'play':
                            if log_time >= 12:
                                log_time, epoch = 0.0, epoch + 1
                            state = 'playing'
                        elif action == 'pause':
                            state = 'paused'
                        elif action == 'stop':
                            state, log_time, epoch = 'ready', 0.0, epoch + 1
                        elif action == 'seek':
                            log_time, epoch = command['time_s'], epoch + 1
                        elif action == 'step':
                            log_time = max(0.0, min(12.0, log_time + command['direction']/60))
                            state, epoch = 'paused', epoch + 1
                        elif action == 'speed':
                            speed = command['speed']
                seq += 1
                q = sample_pose(log_time, list(CANONICAL_JOINTS))
                x, y, yaw = .3*math.sin(log_time/2), .2*(1-math.cos(log_time/2)), .2*math.sin(log_time/3)
                apply(q, (x, y, .015), yaw)
                # Flush articulation transforms, then restore the exact logged pose before rendering.
                world.step(render=False)
                apply(q, (x, y, .015), yaw)
                if args.mode == 'rtsp_sync':
                    read = robot.get_joint_positions(joint_indices=indices)
                    read_pos, read_quat = robot.get_world_pose()
                    if (float(np.max(np.abs(read-np.array(runtime_pose(q, CANONICAL_JOINTS))))) > 1e-5
                        or float(np.max(np.abs(read_pos-np.array([x,y,.015])))) > 1e-5
                        or quaternion_angle_error(read_quat,[math.cos(yaw/2),0,0,math.sin(yaw/2)]) > 1e-5):
                        raise RuntimeError('Readback differs from requested capture pose')
                    wq,xq,yq,zq = map(float,read_quat)
                    expected = capture_stamp(seq)
                    for bit,visible in enumerate(marker_switches):
                        visible.Set('inherited' if expected & (1<<bit) else 'invisible')
                    snapshot = {'sei_schema':'p0.snapshot.v1','session_id':session_id,'epoch':epoch,'render_seq':seq,
                        'log_time_s':log_time,'joints':read.tolist(),'joint_names':list(CANONICAL_JOINTS),
                        'source_joints':q,'chassis':{'x':float(read_pos[0]),'y':float(read_pos[1]),
                            'yaw':math.atan2(2*(wq*zq+xq*yq),1-2*(yq*yq+zq*zq))},
                        'state':state,'speed':speed,'request_id':request_id,'captured_wall_s':time.time(),
                        'marker_pixels':sample_pixels,'marker_bits':64,'marker_layout':'capture32_crc32'}
                    snapshot_writer.arm(seq,snapshot)
                    # 6.2 Real-Time 2.0 requires DLSS/RR. Reset temporal history
                    # after pose/visibility changes so a capture cannot blend
                    # pixels from earlier business frames (official SDG workflow).
                    carb.settings.get_settings().set('/rtx-transient/post/dlss/forceParamReset', True)
                    rep.orchestrator.step(delta_time=0.0,rt_subframes=args.rt_subframes,
                                          pause_timeline=False,wait_for_render=True)
                    _atomic_write(args.output_dir/'latest_snapshot.json',json.dumps(snapshot).encode())
                    trace.write(json.dumps({'snapshot':snapshot,'writer':snapshot_writer.sync_stats})+'\n')
                    trace.flush()
                    capture_times.append(time.monotonic()-begin);jpeg_sizes.append(0)
                    if seq%30==0:print('P0_SYNC',seq,json.dumps(snapshot_writer.sync_stats),flush=True)
                    time.sleep(max(0,1/args.fps-(time.monotonic()-begin)))
                    continue
                if args.mode == 'rtsp':
                    world.render()
                    trace.write(json.dumps({'render_seq': seq, 'log_time_s': log_time,
                        'scope': 'rtsp-server-only', 'browser_verified': False}) + '\n')
                    trace.flush()
                    capture_times.append(time.monotonic()-begin)
                    jpeg_sizes.append(0)
                    time.sleep(max(0, 1/args.fps-(time.monotonic()-begin)))
                    continue
                expected = seq % 4096
                for bit, visible in enumerate(marker_switches):
                    visible.Set('inherited' if expected & (1 << bit) else 'invisible')
                decoded = None
                diagnostic_frames = []
                for render_attempt in range(60 if args.diagnose_capture else 4):
                    world.render()
                    # Explicit capture ticks the annotator without advancing log/physics time.
                    rep.orchestrator.step(delta_time=0.0, rt_subframes=args.rt_subframes, pause_timeline=False)
                    frame = rgb.get_data()
                    if not isinstance(frame, np.ndarray) or frame.size == 0:
                        continue
                    try:
                        samples = [frame[py, px, :3] for px, py in sample_pixels]
                        decoded = decode_marker_rgb(samples)
                    except ValueError:
                        continue
                    if args.diagnose_capture:
                        diagnostic_frames.append({'attempt': render_attempt, 'decoded': decoded,
                            'rgb0': samples[0].tolist(), 'authored0_visible': marker_switches[0].Get(),
                            'physics_time': world.current_time})
                    if decoded == expected:
                        break
                if decoded != expected:
                    report['marker_mismatches'] += 1
                    Image.fromarray(frame).save(args.output_dir / 'marker-failure.png')
                    report['marker_pixels'] = sample_pixels
                    report['marker_samples'] = [frame[py, px, :3].tolist() for px, py in sample_pixels]
                    report['capture_diagnostics'] = diagnostic_frames
                    raise RuntimeError(f'Rendered marker mismatch: wanted {expected}, got {decoded}')
                read = robot.get_joint_positions(joint_indices=indices)
                if float(np.max(np.abs(read-np.array(runtime_pose(q, CANONICAL_JOINTS))))) > 1e-5:
                    raise RuntimeError('Pose changed during capture')
                read_pos, read_quat = robot.get_world_pose()
                if (float(np.max(np.abs(read_pos-np.array([x,y,.015])))) > 1e-5
                    or quaternion_angle_error(read_quat, [math.cos(yaw/2),0,0,math.sin(yaw/2)]) > 1e-5):
                    raise RuntimeError('Chassis pose changed during capture')
                wq, xq, yq, zq = [float(v) for v in read_quat]
                read_yaw = math.atan2(2*(wq*zq+xq*yq), 1-2*(yq*yq+zq*zq))
                captured_wall = time.time()
                meta = {'epoch': epoch, 'render_seq': seq, 'capture_seq': decoded,
                        'log_time_s': log_time, 'joints': read.tolist(), 'source_joints': q,
                        'wheel_policy': 'runtime wrapped to [-pi,pi); source_joints preserves winding',
                        'joint_names': list(CANONICAL_JOINTS),
                        'chassis': {'x': float(read_pos[0]), 'y': float(read_pos[1]), 'yaw': read_yaw},
                        'captured_wall_s': captured_wall, 'state': state, 'speed': speed,
                        'request_id': request_id, 'render_attempts': render_attempt + 1,
                        'marker_pixels': sample_pixels, 'marker_modulus': 4096}
                image = Image.fromarray(frame[:, :, :3])
                ImageDraw.Draw(image).text((16, args.height-24),
                    f'IsaacSim {profile["isaacsim_version"]} | rendered marker={decoded} | epoch={epoch} | log={log_time:.3f}s',
                    fill=(255, 255, 255))
                encoded = io.BytesIO()
                image.save(encoded, format='JPEG', quality=85)
                payload = encoded.getvalue()
                publish_frame(args.output_dir, meta, payload)
                if seq == 1:
                    image.save(args.output_dir / 'first-frame.png')
                trace.write(json.dumps(meta, allow_nan=False) + '\n')
                trace.flush()
                capture_times.append(time.monotonic()-begin)
                jpeg_sizes.append(len(payload))
                if seq % 60 == 0:
                    print('P0_FRAME', seq, round(log_time, 3), state, flush=True)
                time.sleep(max(0, 1/args.fps-(time.monotonic()-begin)))
        elapsed = time.monotonic()-loop_start
        report.update(status='pass', elapsed_seconds=elapsed, last_epoch=epoch,
                      last_request_id=request_id, shutdown_requested=not running,
                      **loop_statistics(args.mode, seq, elapsed, capture_times, jpeg_sizes))
    except Exception as exc:
        import traceback
        traceback.print_exc()
        report.update(status='fail', error=str(exc))
    finally:
        if snapshot_writer is not None:
            report['snapshot_writer'] = snapshot_writer.sync_stats
            transport_error = snapshot_transport_error(report['snapshot_writer'])
            if report['status']=='pass' and transport_error:
                report.update(status='fail',error=transport_error)
            snapshot_writer.detach()
        (args.output_dir / 'results.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        print('P0_RESULT', report['status'], flush=True)
        app.close(wait_for_replicator=False)
    return 0 if report['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())
