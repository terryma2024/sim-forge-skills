"""Blender worker using unmodified upstream SimForge services.

blender -b --factory-startup --python-exit-code 1 --python blender_asset.py -- ACTION RUN_DIRECTORY
"""
import json
import math
from pathlib import Path
import shutil
import sys
import uuid
import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

sys.path.insert(0,str(Path(__file__).resolve().parent))
from common import config_at, sha256, write_json

args=sys.argv[sys.argv.index('--')+1:]; action=args[0]; run=Path(args[1]).resolve()
c=config_at(run); out=run/'assets'; settings=c['blender']; asset=c['asset']
unit=asset['height_m']/2  # Normalize to 2 BU, then export geometry in SI meters.
source=c['simforge']['source_dir']
if not source or not (Path(source).expanduser()/'simforge/core/contracts.py').is_file():
    raise RuntimeError('Pinned SimForge source is missing; run pipeline.py bootstrap first')
sys.path.insert(0,str(Path(source).expanduser()))
from simforge.core.contracts import RetopoOptions,BakeOptions,ExportOptions,PhysicsOptions
from simforge.services.retopo import retopo
from simforge.services.bake import bake
from simforge.services.export import export


def stats(obj):
    bm=bmesh.new(); bm.from_mesh(obj.data)
    data={'vertices':len(bm.verts),'edges':len(bm.edges),'faces':len(bm.faces),
          'quads':sum(len(f.verts)==4 for f in bm.faces),
          'non_manifold_edges':sum(not e.is_manifold for e in bm.edges),
          'dimensions_blender':list(obj.dimensions),'has_uv':obj.data.uv_layers.active is not None}
    bm.free(); return data


def cavity(obj):
    tree=BVHTree.FromObject(obj,bpy.context.evaluated_depsgraph_get())
    bounds=[Vector(v) for v in obj.bound_box]
    minx,maxx=min(v.x for v in bounds),max(v.x for v in bounds)
    miny,maxy=min(v.y for v in bounds),max(v.y for v in bounds)
    top=max(v.z for v in bounds); best=None
    for ix in range(1,20):
        for iy in range(1,20):
            x=minx+(maxx-minx)*ix/20; y=miny+(maxy-miny)*iy/20
            hit,normal,index,distance=tree.ray_cast(Vector((x,y,top+1)),Vector((0,0,-1)))
            if hit is None or not 0<=hit.z<top*.55: continue
            clearances=[]
            for k in range(16):
                angle=2*math.pi*k/16; direction=Vector((math.cos(angle),math.sin(angle),0))
                side,normal,_,distance=tree.ray_cast(Vector((x,y,top*.7)),direction)
                if side is None or normal.dot(direction)>=-.05: break
                clearances.append(distance)
            if len(clearances)==16 and (best is None or min(clearances)>best['clearance_blender']):
                best={'center_xy_blender':[x,y],'floor_z_blender':hit.z,'rim_z_blender':top,
                      'clearance_blender':min(clearances)}
    if best is None or best['clearance_blender']<.12:
        raise RuntimeError('The actual mesh has no verified usable cavity; review the 3D checkpoint rather than substitute geometry')
    best['ball_radius_m']=min(best['clearance_blender']*.4,.33)*unit
    return best


def gpu():
    prefs=bpy.context.preferences.addons['cycles'].preferences
    choices=('METAL','OPTIX','CUDA','HIP','ONEAPI') if settings['gpu_backend']=='AUTO' else (settings['gpu_backend'],)
    for backend in choices:
        try:
            prefs.compute_device_type=backend; prefs.get_devices()
            devices=[d for d in prefs.devices if d.type==backend]
            if devices:
                for d in prefs.devices: d.use=d in devices
                return backend
        except (TypeError,ValueError): pass
    raise RuntimeError('No requested Cycles GPU available; inspect environment initialization and gpu_backend')


def save_scene(filename,report,reportname):
    if not hasattr(bpy.types.Scene,'simforge'):
        import simforge
        simforge.register()
    panel=bpy.context.scene.simforge
    panel.high_poly=bpy.data.objects['Source_High']; panel.low_poly=bpy.data.objects.get('Asset_Low')
    panel.resolution=str(settings['resolution']); panel.cage_extrusion=settings['cage_extrusion']
    panel.texture_directory='//baked_textures/'
    panel.export_path='//asset_convex_decomposition.usda'
    panel.mass_kg=asset['mass_kg']; panel.body_type='DYNAMIC'
    panel.collision_approximation='convexDecomposition'
    panel.static_friction=asset['static_friction']; panel.dynamic_friction=asset['dynamic_friction']
    panel.restitution=asset['restitution']
    for obj in bpy.context.scene.objects: obj.select_set(False)
    active=panel.low_poly or panel.high_poly
    active.hide_set(False); active.select_set(True); bpy.context.view_layer.objects.active=active
    bpy.ops.file.pack_all(); bpy.ops.wm.save_as_mainfile(filepath=str(out/filename))
    write_json(out/reportname,report)


def preview(filename,comparison=False):
    """Render a new scene without changing the delivered Blender checkpoint."""
    scene=bpy.data.scenes.new('Asset Review'); bpy.context.window.scene=scene
    scene.render.engine='CYCLES'; scene.cycles.device='GPU'; scene.cycles.samples=settings['samples']
    scene.cycles.use_denoising=True; gpu()
    originals=[('Source_High',0)] if not comparison else [('Source_High',-1.65),('Asset_Low',1.65)]
    width=3.6
    for name,x in originals:
        original=bpy.data.objects[name]; obj=original.copy(); scene.collection.objects.link(obj)
        obj.hide_render=False; obj.hide_viewport=False; obj.hide_set(False)
        bounds=[Vector(v) for v in obj.bound_box]
        center=Vector(((min(v.x for v in bounds)+max(v.x for v in bounds))/2,
                       (min(v.y for v in bounds)+max(v.y for v in bounds))/2,0))
        obj.location=Vector((x,0,0))-center
        width=max(width,obj.dimensions.x*1.4)
    world=bpy.data.worlds.new('Studio'); world.use_nodes=True
    background=next(n for n in world.node_tree.nodes if n.type=='BACKGROUND')
    background.inputs['Color'].default_value=(.7,.75,.8,1); background.inputs['Strength'].default_value=.35
    scene.world=world
    bpy.ops.mesh.primitive_plane_add(size=200,location=(0,0,-.02))
    floor=bpy.data.materials.new('Studio Floor'); floor.use_nodes=True
    next(n for n in floor.node_tree.nodes if n.type=='BSDF_PRINCIPLED').inputs['Base Color'].default_value=(.32,.36,.4,1)
    bpy.context.object.data.materials.append(floor)
    for name,position,power,size in [('Key',(-3,-4,6),600,5),('Fill',(4,-1,4),350,4),('Rim',(0,4,5),450,4)]:
        light=bpy.data.lights.new(name,'AREA'); light.energy=power; light.size=size
        obj=bpy.data.objects.new(name,light); scene.collection.objects.link(obj); obj.location=position
        obj.rotation_euler=(Vector((0,0,1))-obj.location).to_track_quat('-Z','Y').to_euler()
    data=bpy.data.cameras.new('Review Camera'); data.type='ORTHO'; data.ortho_scale=max(width,7.2 if comparison else 4)
    camera=bpy.data.objects.new('Review Camera',data); scene.collection.objects.link(camera)
    camera.location=(0,-7.5,6); camera.rotation_euler=(Vector((0,0,.95))-camera.location).to_track_quat('-Z','Y').to_euler()
    scene.camera=camera; scene.render.resolution_x=1500; scene.render.resolution_y=950
    scene.render.resolution_percentage=100; scene.render.filepath=str(out/filename)
    scene.render.image_settings.file_format='PNG'; bpy.ops.render.render(write_still=True)


def validate(report):
    from pxr import Usd,UsdGeom,UsdPhysics,UsdShade,Sdf
    provenance=json.loads((out/'source_provenance.json').read_text())
    assert sha256(out/'source.glb')==provenance['glb_sha256']
    textures={}
    for channel,filename in report['textures'].items():
        path=out/'baked_textures'/filename; image=bpy.data.images.load(str(path),check_existing=False)
        assert list(image.size)==[settings['resolution']]*2
        textures[channel]={'file':filename,'resolution':list(image.size),'sha256':sha256(path)}
        bpy.data.images.remove(image)
    assert set(textures)=={'Normal','Diffuse','Metallic','Roughness','Alpha'}
    variants={}; point_arrays=[]
    for mode,filename in report['usd_assets'].items():
        stage=Usd.Stage.Open(str(out/filename)); root=stage.GetDefaultPrim()
        assert UsdGeom.GetStageMetersPerUnit(stage)==1 and UsdGeom.GetStageUpAxis(stage)==UsdGeom.Tokens.z
        assert UsdPhysics.GetStageKilogramsPerUnit(stage)==1
        assert root.HasAPI(UsdPhysics.RigidBodyAPI) and not UsdPhysics.RigidBodyAPI(root).GetKinematicEnabledAttr().Get()
        assert abs(UsdPhysics.MassAPI(root).GetMassAttr().Get()-asset['mass_kg'])<1e-6
        meshes=[p for p in stage.Traverse() if p.IsA(UsdGeom.Mesh)]; assert len(meshes)==1
        mesh=meshes[0]; assert UsdPhysics.MeshCollisionAPI(mesh).GetApproximationAttr().Get()==mode
        physical,_=UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial('physics')
        visual,_=UsdShade.MaterialBindingAPI(mesh).ComputeBoundMaterial(); assert physical and visual
        material=UsdPhysics.MaterialAPI(physical.GetPrim())
        for getter,key in [('GetStaticFrictionAttr','static_friction'),('GetDynamicFrictionAttr','dynamic_friction'),('GetRestitutionAttr','restitution')]:
            assert abs(getattr(material,getter)().Get()-asset[key])<1e-6
        resources=[]
        for prim in stage.Traverse():
            for attr in prim.GetAttributes():
                if attr.GetTypeName()==Sdf.ValueTypeNames.Asset:
                    value=attr.Get()
                    if value and value.path:
                        path=Path(value.path); assert not path.is_absolute() and '..' not in path.parts
                        assert (out/path).is_file(); resources.append(path.as_posix())
        assert len(set(resources))==5
        points=UsdGeom.Mesh(mesh).GetPointsAttr().Get(); point_arrays.append([list(p) for p in points])
        dimensions=[max(p[i] for p in points)-min(p[i] for p in points) for i in range(3)]
        assert abs(dimensions[2]-asset['height_m'])<asset['height_m']*.02
        variants[mode]={'file':filename,'mass_kg':asset['mass_kg'],'dimensions_m':dimensions,'textures':resources}
    assert point_arrays[0]==point_arrays[1]
    result={'passed':True,'scope':'Source preservation, retopology, PBR, USD schemas and resources',
            'source_sha256':provenance['glb_sha256'],'high_faces':report['high']['faces'],
            'low_faces':report['low']['faces'],'low_non_manifold_edges':report['low']['non_manifold_edges'],
            'textures':textures,'variants':variants}
    write_json(out/'asset_validation.json',result)


if action=='import':
    bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(out/'source.glb'))
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH']; assert meshes
    for obj in tuple(bpy.context.selected_objects): obj.select_set(False)
    for obj in meshes: obj.select_set(True)
    bpy.context.view_layer.objects.active=meshes[0]; bpy.ops.object.join(); high=bpy.context.active_object
    matrix=high.matrix_world.copy(); high.parent=None; high.matrix_world=matrix
    bpy.ops.object.transform_apply(location=True,rotation=True,scale=True); high.name='Source_High'
    zs=[v.co.z for v in high.data.vertices]; floor,ceiling=min(zs),max(zs)
    if ceiling<=floor: raise ValueError('Source has zero height')
    for vertex in high.data.vertices: vertex.co.z-=floor; vertex.co*=2/(ceiling-floor)
    high.data.update(); bpy.context.view_layer.update()
    bpy.context.scene.unit_settings.system='METRIC'; bpy.context.scene.unit_settings.scale_length=unit
    for axis in ('x','y','z'): setattr(high.data,'use_mirror_'+axis,False)
    report={'stage':'import','source':'user-supplied or Seed3D','source_glb_sha256':sha256(out/'source.glb'),
            'meters_per_blender_unit':unit,'high':stats(high)}
    if asset['require_cavity']:
        try: report.update(high_cavity=cavity(high),cavity_verified=True)
        except RuntimeError as error: report.update(cavity_verified=False,cavity_error=str(error))
    save_scene('source.blend',report,'source_report.json'); preview('source_preview.png')
elif action=='retopo':
    bpy.ops.wm.open_mainfile(filepath=str(out/'source.blend')); report=json.loads((out/'source_report.json').read_text())
    high=bpy.data.objects['Source_High']; before=stats(high)
    low=retopo(bpy.context,high,RetopoOptions(target_faces=settings['target_faces'],voxel_size=settings['voxel_size'])).low_poly
    low.name='Asset_Low'; assert stats(high)==before
    report.update(stage='retopo',low=stats(low)); assert report['low']['has_uv']
    assert report['low']['faces']<report['high']['faces'] and report['low']['non_manifold_edges']==0
    if asset['require_cavity']:
        try: report.update(low_cavity=cavity(low),cavity_verified=True)
        except RuntimeError as error: report.update(cavity_verified=False,cavity_error=str(error))
    high.hide_render=True; high.hide_set(True)
    save_scene('retopo.blend',report,'retopo_report.json')
    if report.get('cavity_verified') is False: preview('retopo_preview.png',True)
elif action=='bake':
    bpy.ops.wm.open_mainfile(filepath=str(out/'retopo.blend')); report=json.loads((out/'retopo_report.json').read_text())
    backend=gpu(); bpy.context.scene.cycles.samples=settings['samples']
    temporary=run/'.attempts'/('bake-'+uuid.uuid4().hex[:8]); temporary.mkdir(parents=True)
    result=bake(bpy.context,bpy.data.objects['Source_High'],bpy.data.objects['Asset_Low'],
        BakeOptions(resolution=settings['resolution'],cage_extrusion=settings['cage_extrusion']),temporary/'baked_textures')
    destination=out/'baked_textures'
    if destination.exists(): raise ValueError('Completed textures exist; inspect the previous attempt before replacing')
    (temporary/'baked_textures').rename(destination)
    for image in bpy.data.images:
        if Path(image.filepath).name in [p.name for p in result.textures.values()]:
            image.filepath=str(destination/Path(image.filepath).name)
    report.update(stage='bake',gpu_backend=backend,textures={k.value:p.name for k,p in result.textures.items()})
    assert len(report['textures'])==5
    save_scene('baked.blend',report,'bake_report.json')
elif action=='export':
    bpy.ops.wm.open_mainfile(filepath=str(out/'baked.blend')); report=json.loads((out/'bake_report.json').read_text())
    temporary=run/'.attempts'/('export-'+uuid.uuid4().hex[:8]); temporary.mkdir(parents=True)
    exported={}
    for mode,name in [('convexDecomposition','asset_convex_decomposition.usda'),('convexHull','asset_convex_hull.usda')]:
        destination=temporary/name
        export(bpy.context,bpy.data.objects['Asset_Low'],ExportOptions(destination,
            PhysicsOptions(body_type='DYNAMIC',mass_kg=asset['mass_kg'],collision_approximation=mode,
                static_friction=asset['static_friction'],dynamic_friction=asset['dynamic_friction'],restitution=asset['restitution']),
            meters_per_blender_unit=unit))
        exported[mode]=name
    for path in temporary.iterdir():
        if (out/path.name).exists(): raise ValueError('Export artifact already exists; preserve previous outputs and inspect the attempt')
        shutil.move(str(path),out/path.name)
    report.update(stage='export',usd_assets=exported)
    save_scene('asset_pipeline.blend',report,'pipeline_report.json'); validate(report); preview('high_low_comparison.png',True)
else:
    raise ValueError('Unknown Blender action')
print('SIMFORGE_WORKER_OK',action,flush=True)
