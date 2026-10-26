"""IsaacSim 6.2/PhysX kinematic executor, migrated from tested P0 capture code."""
from pathlib import Path
import json,math,os,sys,time,zlib
from replay_protocol import CANONICAL_JOINTS,AppliedFrame
from .config import load_asset_profile
from .asset_audit import audit_asset
from .media import SnapshotRegistry,create_snapshot_writer,SEI_METADATA_UUID


def physics_launch_args():
    return ['--/exts/isaacsim.physics.newton/auto_switch_on_startup=false','--/exts/isaacsim.core.simulation_manager/default_engine=physx','--/app/settings/persistent=false']


def runtime_pose(values):
    return [((v+math.pi)%(2*math.pi)-math.pi) if n.startswith('wheel_motor_joint') else v for n,v in zip(CANONICAL_JOINTS,values)]


def quat_error(a,b):
    an=math.sqrt(sum(float(x)**2 for x in a));bn=math.sqrt(sum(float(x)**2 for x in b))
    if not an or not bn:raise ValueError('Zero quaternion is not a valid orientation')
    dot=abs(sum(float(x)*float(y) for x,y in zip(a,b))/(an*bn))
    return 2*math.acos(max(-1.,min(1.,dot)))


def orientation(yaw,initial):
    w,x,y,z=map(float,initial)
    roll=math.atan2(2*(w*x+y*z),1-2*(x*x+y*y))
    pitch=math.asin(max(-1.,min(1.,2*(w*y-z*x))))
    cr,sr=math.cos(roll/2),math.sin(roll/2);cp,sp=math.cos(pitch/2),math.sin(pitch/2);cy,sy=math.cos(yaw/2),math.sin(yaw/2)
    return [cr*cp*cy+sr*sp*sy,sr*cp*cy-cr*sp*sy,cr*sp*cy+sr*cp*sy,cr*cp*sy-sr*sp*cy]


class Runtime62:
    def __init__(self,config,project_root,output_dir,*,on_phase=None):
        self.config=config;self.output_dir=Path(output_dir);self.app=None;self.writer=None;self.trace=None
        self.on_phase=on_phase or (lambda phase:None)
        profile=load_asset_profile(Path(project_root));asset=profile['asset_path']
        audit=audit_asset(asset)
        if not audit['passed']:raise RuntimeError('Robot asset audit failed: '+str(audit['errors']))
        self.report={'isaacsim_expected':'6.2.0','asset_audit':audit,'metadata_uuid_hex':SEI_METADATA_UUID.hex(),'rtsp_url':f'rtsp://127.0.0.1:{config.rtsp_port}/stream','capture_count':0}
        try:self._start(config,profile)
        except BaseException as exc:
            self.report['startup_error']=f'{type(exc).__name__}: {exc}'
            print('WORKER_STARTUP_ERROR',self.report['startup_error'],file=sys.stderr,flush=True)
            self.close(exit_code=1)
            raise
    def _start(self,config,profile):
        from isaacsim import SimulationApp
        self.app=SimulationApp({'headless':True,'width':config.width,'height':config.height,
            'window_width':config.width,'window_height':config.height,'renderer':'RaytracedLighting',
            'limit_cpu_threads':12,'disable_viewport_updates':True,'hide_ui':True,'anti_aliasing':0,
            'samples_per_pixel_per_frame':1,'denoiser':False,'extra_args':physics_launch_args()},
            experience=str(Path(os.environ['EXP_PATH'])/'isaacsim.exp.base.zero_delay.kit'))
        import numpy as np
        import carb
        from pxr import Gf,UsdGeom,UsdLux,UsdPhysics,UsdShade,PhysicsSchemaTools,UsdUtils
        import omni.usd
        import omni.replicator.core as rep
        from isaacsim.core.api import World
        from isaacsim.core.prims import SingleArticulation
        from isaacsim.core.utils.viewports import set_camera_view
        from isaacsim.core.version import get_version
        from isaacsim.core.simulation_manager import SimulationManager
        self.np=np;self.rep=rep;self.carb=carb
        version=get_version()[0];engine=SimulationManager.get_active_physics_engine()
        if version!='6.2.0' or engine.lower()!='physx':raise RuntimeError(f'Expected 6.2.0/PhysX, got {version}/{engine}')
        self.report.update(isaacsim_version=version,physics_backend=engine)
        self.on_phase('loading_scene')
        world=World(stage_units_in_meters=1,physics_dt=1/60,rendering_dt=1/config.render_hz,backend='numpy')
        self.world=world;stage=omni.usd.get_context().get_stage()
        entry=profile['asset_path']/profile['entrypoint']
        self.on_phase('loading_robot')
        stage.DefinePrim('/World/Robot','Xform').GetReferences().AddReference(str(entry))
        PhysicsSchemaTools.addGroundPlane(stage,'/World/Ground','Z',20,Gf.Vec3f(0),Gf.Vec3f(.35))
        UsdLux.DomeLight.Define(stage,'/World/Light').CreateIntensityAttr(800)
        sun=UsdLux.DistantLight.Define(stage,'/World/Sun');sun.CreateIntensityAttr(1800)
        UsdGeom.Xformable(sun).AddRotateXYZOp().Set(Gf.Vec3f(-35,25,25))
        roots=[p for p in stage.Traverse() if p.HasAPI(UsdPhysics.ArticulationRootAPI)]
        if len(roots)!=1 or str(roots[0].GetPath())!=profile['referenced_runtime_root']:raise RuntimeError('Unexpected articulation root')
        self.robot=world.scene.add(SingleArticulation(str(roots[0].GetPath()),name='robot'))
        world.reset();world.step(render=False)
        if SimulationManager.get_active_physics_engine().lower()!='physx':raise RuntimeError('Physics engine changed after reset')
        names=list(self.robot.dof_names)
        if len(names)!=28 or set(names)!=set(CANONICAL_JOINTS):raise RuntimeError('Actual DOFs differ from canonical robot')
        self.indices=np.array([names.index(n) for n in CANONICAL_JOINTS])
        layers,assets,unresolved=UsdUtils.ComputeAllDependencies(str(entry))
        outside=[str(p) for p in [*(x.realPath for x in layers),*assets] if p and not Path(str(p)).resolve().is_relative_to(profile['asset_path'])]
        if outside or unresolved:raise RuntimeError(f'Nonlocal/unresolved USD dependencies: {outside} {unresolved}')
        self.report.update(articulation_root=profile['referenced_runtime_root'],dof_names=names,canonical_order=list(CANONICAL_JOINTS))
        self.initial_pos=np.array([0.,0.,.015]);self.initial_quat=self.robot.get_world_pose()[1].copy()
        self.robot.set_world_pose(self.initial_pos,self.initial_quat)
        camera=UsdGeom.Camera.Define(stage,'/World/Camera');camera.CreateClippingRangeAttr(Gf.Vec2f(.01,100))
        camera.CreateFocalLengthAttr(35);camera.CreateHorizontalApertureAttr(36);camera.CreateVerticalApertureAttr(36*config.height/config.width)
        set_camera_view(eye=np.array([3.1,2.7,2.1]),target=np.array([0,0,.85]),camera_prim_path='/World/Camera')
        product=rep.create.render_product('/World/Camera',(config.width,config.height),force_new=True)
        # Actual scene geometry encodes render_seq; read pixels independently of metadata.
        marker_switches = []
        sample_pixels = []
        marker_bits = 64
        for bit in range(marker_bits):
            if marker_bits == 64:
                x = -.475 + (bit % 32)*.03
                y = (.32-(bit//32)*.04)*config.height/config.width*4/3
                size = .022
            else:
                x,y,size = -.44+bit*.055, .32*config.height/config.width*4/3, .035
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
            sample_pixels.append([round((x / (18/35) + 1) * config.width/2),
                                  round((1 - y/(18/35 * config.height/config.width)) * config.height/2)])

        self.marker_switches=marker_switches;self.marker_pixels=sample_pixels;self.capture_id=0
        # Capture zero is a reserved, explicitly unarmed warmup image.
        self._marker(0)
        common=json.loads((profile['asset_path']/profile['common_config']).read_text())
        limits=[{'type':common['joints'][n]['type'],**{'lower':common['joints'][n]['limit'].get('lower'),'upper':common['joints'][n]['limit'].get('upper')}} for n in CANONICAL_JOINTS]
        self.trace=(self.output_dir/'rendered.jsonl').open('w')
        def verified(rendered,snapshot,raw):
            self.report['capture_count']+=1
            self.trace.write(json.dumps({'frame':rendered.model_dump(mode='json'),'snapshot':snapshot.model_dump(mode='json')})+'\n');self.trace.flush()
            if self.report['capture_count']<=4:
                from PIL import Image
                value=raw.get('data') if isinstance(raw,dict) else raw
                Image.fromarray(value.copy()).save(self.output_dir/f'capture-{rendered.capture_id:06d}.png')
        self.registry=SnapshotRegistry(sample_pixels,limits,on_verified=verified)
        # Warm the scene before attaching the encoder: never discard its initial
        # SPS/IDR outputs while the snapshot catalog is still unarmed.
        for _ in range(8):world.render()
        self.on_phase('rtsp_publishing')
        self.writer=create_snapshot_writer(stage,product,'/World/Camera',port=config.rtsp_port,width=config.width,height=config.height,registry=self.registry,allow_native_wildcard=config.allow_native_rtsp_wildcard)
        self.report['rtsp_binding_addresses']=self.writer.binding_addresses
        self.report['native_rtsp_wildcard_override']=config.allow_native_rtsp_wildcard
        self.report['marker_pixels']=sample_pixels
        (self.output_dir/'runtime.json').write_text(json.dumps(self.report,indent=2))
    def _marker(self,id):
        stamp=id|(zlib.crc32(id.to_bytes(4,'big'))<<32)
        for bit,visible in enumerate(self.marker_switches):visible.Set('inherited' if stamp&(1<<bit) else 'invisible')
    def apply(self,frame):
        self._requested=frame
        np=self.np;values=np.array(runtime_pose(frame.joints));position=self.initial_pos.copy();quaternion=self.initial_quat.copy()
        if frame.chassis is not None:
            position[:2]=[frame.chassis.x,frame.chassis.y];quaternion=np.array(orientation(frame.chassis.yaw,self.initial_quat))
        def set_pose():
            self.robot.set_world_pose(position,quaternion)
            self.robot.set_joint_positions(values,joint_indices=self.indices);self.robot.set_joint_velocities(np.zeros(28))
            self.robot.set_linear_velocity(np.zeros(3));self.robot.set_angular_velocity(np.zeros(3))
        set_pose();self.world.step(render=False);set_pose()
        read=self.robot.get_joint_positions(joint_indices=self.indices);xyz,quat=self.robot.get_world_pose()
        errors={'joints':float(np.max(np.abs(read-values))),'position':float(np.max(np.abs(xyz-position))),'orientation':quat_error(quat,quaternion)}
        if not np.all(np.isfinite(read)) or any(value>1e-5 for value in errors.values()):
            (self.output_dir/'readback-failure.json').write_text(json.dumps({'errors':errors,'actual_quaternion':quat.tolist(),'requested_quaternion':quaternion.tolist(),'actual_position':xyz.tolist(),'requested_position':position.tolist(),'actual_joints':read.tolist(),'requested_joints':values.tolist()},indent=2))
            raise RuntimeError('Articulation readback mismatch: '+str(errors))
        pose=None
        if frame.chassis is not None:
            w,x,y,z=map(float,quat);pose={'x':float(xyz[0]),'y':float(xyz[1]),'yaw':math.atan2(2*(w*z+x*y),1-2*(y*y+z*z))}
        payload=frame.model_dump(mode='python');payload.update(joints=read.tolist(),chassis=pose,source_joints=frame.joints,applied_wall_s=time.time())
        return AppliedFrame.model_validate(payload)
    def render(self,applied):
        # Replicator delta_time=0 holds the business pose. Advance the render
        # fabric clock and restore/read back that exact target even while paused,
        # matching the proven P0 capture sequence instead of reusing old readback.
        if (applied.session_id,applied.target_seq)!=(self._requested.session_id,self._requested.target_seq):
            raise RuntimeError('Capture target differs from the applied request')
        applied=self.apply(self._requested)
        self.capture_id+=1
        if self.capture_id>0xffffffff:raise RuntimeError('Capture ID exhausted; recreate worker')
        self._marker(self.capture_id);self.writer.arm(self.capture_id,applied)
        for _ in range(8):
            self.carb.settings.get_settings().set('/rtx-transient/post/dlss/forceParamReset',True)
            self.rep.orchestrator.step(delta_time=0.,rt_subframes=1,pause_timeline=False,wait_for_render=True)
            if self.writer._stream_failed:raise RuntimeError('RTSP writer failed')
            result=self.registry.rendered(self.capture_id)
            if result is not None:return result
        raise RuntimeError('Actual raw render identity could not be verified')
    def close(self,exit_code=0):
        from .resources import release_all
        writer,trace,app=self.writer,self.trace,self.app
        self.writer=self.trace=self.app=None
        def save_report():
            if hasattr(self,'registry'):
                self.report['registry']=self.registry.stats()
                if writer is not None:self.report['writer']=writer.sync_stats
                (self.output_dir/'runtime-final.json').write_text(json.dumps(self.report,indent=2))
        actions=[('runtime-report',save_report)]
        if writer is not None:actions.extend([('writer-detach',writer.detach),('writer-stop',writer._stop_server)])
        if trace is not None:actions.append(('trace-close',trace.close))
        errors=release_all(actions)
        if errors:print('RUNTIME_CLEANUP_ERRORS',json.dumps(errors),file=sys.stderr,flush=True)
        # Kit fast shutdown may terminate the process inside close(). Preserve
        # failure status and finish all diagnostics/resources before that call.
        if app is not None:errors.extend(release_all([('app-close',lambda:app.close(exit_code=1 if errors else exit_code))]))
        return errors
