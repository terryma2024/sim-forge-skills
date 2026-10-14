"""Actual asset drop / hollow-cup comparison in Isaac Sim 6.x.

isaacsim-environment/python isaac_verify.py ASSET_DIRECTORY
"""
from pathlib import Path
import json
import math
import sys
from isaacsim import SimulationApp

assets = Path(sys.argv[1]).resolve()
report = json.loads((assets / 'pipeline_report.json').read_text())
config = json.loads((assets / 'runtime_config.json').read_text())
options = config['isaac']
cup_test = options['test'] == 'cup-comparison'
hz = options['physics_hz']
frame_count = round(hz * options['duration_s'])
record_interval = max(1, round(hz / 30))
scale = config['asset']['height_m'] / .09
assert report['stage'] == 'export'
scene_path = assets / 'cup_comparison_scene.usda'
assert not scene_path.exists(), 'Preserve previous runs; use a fresh directory'
app = SimulationApp({'headless': True, 'renderer': 'RayTracedLighting', 'sync_loads': True,
                     'width': 1280, 'height': 720})

def save_result(value):
    path = assets / 'simulation_result.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)

try:
    import numpy as np
    import carb
    import omni.usd
    import omni.timeline
    import omni.replicator.core as rep
    from PIL import Image
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdPhysics, UsdShade, PhysxSchema
    from isaacsim.core.experimental.prims import RigidPrim
    from isaacsim.core.experimental.utils import app as app_utils
    from isaacsim.core.experimental.utils import stage as stage_utils
    from isaacsim.core.simulation_manager import SimulationManager

    stage = Usd.Stage.CreateNew(str(scene_path))
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    stage.SetTimeCodesPerSecond(hz)
    stage.SetFramesPerSecond(hz)
    UsdPhysics.SetStageKilogramsPerUnit(stage, 1)
    world = UsdGeom.Xform.Define(stage, '/World')
    stage.SetDefaultPrim(world.GetPrim())

    def material(name, color, roughness=.45):
        mat = UsdShade.Material.Define(stage, '/World/Materials/' + name)
        shader = UsdShade.Shader.Define(stage, mat.GetPath().AppendChild('Shader'))
        shader.CreateIdAttr('UsdPreviewSurface')
        shader.CreateInput('diffuseColor', Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput('roughness', Sdf.ValueTypeNames.Float).Set(roughness)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), 'surface')
        return mat

    ground = UsdGeom.Cube.Define(stage, '/World/Ground')
    ground.CreateSizeAttr(1)
    ground.AddTranslateOp().Set(Gf.Vec3d(0, 0, -.005 * scale))
    ground.AddScaleOp().Set(Gf.Vec3d(.65 * scale, .6 * scale, .01 * scale))
    UsdPhysics.CollisionAPI.Apply(ground.GetPrim())
    UsdShade.MaterialBindingAPI.Apply(ground.GetPrim()).Bind(material('Ground', (.12, .16, .20)))
    ball_material = material('Ball', (.06, .35, .85), .28)
    cavity = report.get('low_cavity')
    unit = report['meters_per_blender_unit']
    if cup_test and not cavity:
        raise ValueError('Cup comparison needs the measured low-poly cavity')
    center = [v * unit for v in cavity['center_xy_blender']] if cup_test else [0, 0]
    radius = cavity['ball_radius_m'] if cup_test else 0
    rim_height = cavity['rim_z_blender'] * unit if cup_test else config['asset']['height_m']
    floor_height = cavity['floor_z_blender'] * unit if cup_test else 0
    cases = [('Decomposition', -.085 * scale, 'convexDecomposition'), ('Hull', .085 * scale, 'convexHull')]
    initial_offset = .045 * scale
    for name, x, approximation in cases:
        cup = UsdGeom.Xform.Define(stage, '/World/' + name + '/Cup')
        asset_name = report['usd_assets'][approximation]
        assert (assets / asset_name).is_file()
        cup.GetPrim().GetReferences().AddReference('./' + asset_name)
        cup.ClearXformOpOrder()
        cup.AddTranslateOp().Set(Gf.Vec3d(x - center[0], -center[1], initial_offset))
        cup.GetPrim().CreateAttribute('reproduction:collisionMode', Sdf.ValueTypeNames.String).Set(approximation)
        assert cup.GetPrim().HasAPI(UsdPhysics.RigidBodyAPI)
        bodies = [cup.GetPrim()]
        if cup_test:
            ball = UsdGeom.Sphere.Define(stage, '/World/' + name + '/Ball')
            ball.CreateRadiusAttr(radius)
            ball.AddTranslateOp().Set(Gf.Vec3d(x, 0, initial_offset + rim_height + .06 * scale))
            UsdPhysics.CollisionAPI.Apply(ball.GetPrim())
            UsdPhysics.RigidBodyAPI.Apply(ball.GetPrim())
            UsdPhysics.MassAPI.Apply(ball.GetPrim()).CreateMassAttr(.02)
            UsdShade.MaterialBindingAPI.Apply(ball.GetPrim()).Bind(ball_material)
            bodies.append(ball.GetPrim())
        for body in bodies:
            rigid = PhysxSchema.PhysxRigidBodyAPI.Apply(body)
            rigid.CreateEnableCCDAttr(True)
            rigid.CreateSolverPositionIterationCountAttr(32)
            rigid.CreateSolverVelocityIterationCountAttr(4)
        if cup_test:
            # This is explicit user configuration, never a hidden universal default.
            PhysxSchema.PhysxRigidBodyAPI(ball.GetPrim()).CreateAngularDampingAttr(options['ball_angular_damping'])
        for prim in Usd.PrimRange(cup.GetPrim()):
            if prim.HasAPI(UsdPhysics.CollisionAPI):
                collision = PhysxSchema.PhysxCollisionAPI.Apply(prim)
                collision.CreateContactOffsetAttr(options['contact_offset_m'])
                collision.CreateRestOffsetAttr(0)
        if cup_test:
            collision = PhysxSchema.PhysxCollisionAPI.Apply(ball.GetPrim())
            collision.CreateContactOffsetAttr(options['contact_offset_m'])
            collision.CreateRestOffsetAttr(0)
    physics = UsdPhysics.Scene.Define(stage, '/World/PhysicsScene')
    physics.CreateGravityDirectionAttr(Gf.Vec3f(0, 0, -1))
    physics.CreateGravityMagnitudeAttr(9.81)
    PhysxSchema.PhysxSceneAPI.Apply(physics.GetPrim()).CreateEnableCCDAttr(True)
    dome = UsdLux.DomeLight.Define(stage, '/World/Dome')
    dome.CreateIntensityAttr(600)
    key = UsdLux.DistantLight.Define(stage, '/World/Key')
    key.CreateIntensityAttr(2200)
    key.AddRotateXYZOp().Set(Gf.Vec3f(315, 0, 30))
    camera = UsdGeom.Camera.Define(stage, '/World/Camera')
    camera.CreateFocalLengthAttr(28)
    camera.CreateHorizontalApertureAttr(24)
    camera.CreateVerticalApertureAttr(13.5)
    camera.CreateClippingRangeAttr(Gf.Vec2f(.001, 100))
    view = Gf.Matrix4d().SetLookAt(Gf.Vec3d(0, -.18 * scale, .50 * scale), Gf.Vec3d(0, 0, .07 * scale), Gf.Vec3d(0, 0, 1)).GetInverse()
    camera.AddTransformOp().Set(view)
    stage.GetRootLayer().Save()
    omni.usd.get_context().open_stage(str(scene_path))
    while stage_utils.is_stage_loading():
        app.update()
    timeline = omni.timeline.get_timeline_interface()
    carb.settings.get_settings().set('/app/player/useFixedTimeStepping', True)
    carb.settings.get_settings().set('/app/player/CompensatePlayDelayInSecs', 0.0)
    timeline.set_play_every_frame(True)
    timeline.set_target_framerate(hz)
    product = rep.create.render_product('/World/Camera', (1280, 720))
    rep.orchestrator.set_capture_on_play(True)
    rgb = rep.AnnotatorRegistry.get_annotator('rgb')
    rgb.attach(product)
    rep.orchestrator.step(rt_subframes=4, delta_time=0.0, pause_timeline=True)
    app_utils.stop()
    for _ in range(20):
        app.update()
    app_utils.stop()
    SimulationManager.setup_simulation(dt=1 / hz, device='cpu')
    app_utils.play()
    app.update()
    cup_actors = {n: RigidPrim('/World/' + n + '/Cup') for n, _, _ in cases}
    ball_actors = {n: RigidPrim('/World/' + n + '/Ball') for n, _, _ in cases} if cup_test else {}
    frames = assets / 'simulation_frames'
    frames.mkdir(exist_ok=True)
    history = {n: [] for n, _, _ in cases}
    image_index = 0
    for frame in range(frame_count):
        app.update()
        for name, _, _ in cases:
            position, orientation = cup_actors[name].get_world_poses()
            cup_position = position.numpy()[0]
            cup_quaternion = orientation.numpy()[0]
            sample = {'frame': frame, 'time_s': SimulationManager.get_simulation_time(),
                'cup_position': cup_position.tolist(),
                'cup_quaternion': cup_quaternion.tolist()}
            if cup_test:
                ball_position = ball_actors[name].get_world_poses()[0].numpy()[0]
                q = Gf.Quatd(float(cup_quaternion[0]), Gf.Vec3d(*[float(v) for v in cup_quaternion[1:]]))
                local = Gf.Rotation(q).GetInverse().TransformDir(Gf.Vec3d(*[float(v) for v in ball_position - cup_position]))
                sample.update(ball_position=ball_position.tolist(), ball_in_cup_coordinates=list(local))
            history[name].append(sample)
        if frame % record_interval == 0:
            pixels = rgb.get_data()
            if isinstance(pixels, np.ndarray) and pixels.ndim == 3 and pixels.shape[2] >= 3:
                Image.fromarray(pixels[:, :, :3]).save(frames / f'frame_{image_index:04d}.png')
                image_index += 1
        if frame % 60 == 0:
            print('CUP_SIM_FRAME', frame, flush=True)
    metrics = {}
    for name, _, approximation in cases:
        samples = history[name]
        last = samples[-1]
        z = last['ball_in_cup_coordinates'][2] if cup_test else 0
        radial = math.hypot(last['ball_in_cup_coordinates'][0] - center[0],
                            last['ball_in_cup_coordinates'][1] - center[1]) if cup_test else 0
        clearance = cavity['clearance_blender'] * unit if cup_test else 0
        cup_drop = initial_offset - last['cup_position'][2]
        inside = cup_test and (floor_height - .002 * scale < z - radius and z + radius < rim_height - .003 * scale
                  and radial + radius < clearance + .002 * scale)
        above = cup_test and z - radius > rim_height - .003 * scale
        field = 'ball_in_cup_coordinates' if cup_test else 'cup_position'
        recent = np.array([s[field] for s in samples[-max(2, round(hz*.5)):]])
        movement = float(np.linalg.norm(np.ptp(recent, axis=0)))
        stable = movement < options['stability_tolerance_m']
        metrics[name] = {'collision': approximation, 'cup_drop_m': cup_drop,
            'ball_relative_height_m': z, 'ball_radius_m': radius,
            'ball_radial_offset_m': radial, 'cavity_clearance_m': clearance,
            'cavity_floor_m': floor_height, 'rim_height_m': rim_height,
            'inside_cup': inside, 'above_opening': above, 'settled': stable,
            'last_half_second_movement_m': movement}
    import importlib.metadata
    result = {'source': 'Actual Isaac Sim dynamics', 'isaac_version': importlib.metadata.version('isaacsim'),
              'test': options['test'], 'physics_dt': 1/hz, 'recording_fps': hz/record_interval,
              'initial_cup_height_m': initial_offset, 'ccd_enabled': True,
              'ball_angular_damping_per_s': options['ball_angular_damping'], 'metrics': metrics,
              'frames_saved': image_index, 'trajectories': history}
    assert all(m['cup_drop_m'] > initial_offset*.5 for m in metrics.values()), metrics
    assert all(m['settled'] for m in metrics.values()), metrics
    if cup_test:
        assert metrics['Decomposition']['inside_cup'], metrics
        assert metrics['Hull']['above_opening'], metrics
    assert image_index == len(range(0,frame_count,record_interval)), 'Visual evidence is incomplete'
    result['passed'] = True
    save_result(result)
    print('ISAAC_ASSET_VERIFICATION_OK', json.dumps(metrics), flush=True)
    app_utils.stop()
except Exception:
    import traceback
    if 'result' in locals():
        result['passed'] = False
        save_result(result)
    traceback.print_exc()
    raise
finally:
    app.close()
