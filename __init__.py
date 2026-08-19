bl_info = {
    "name": "Sculpt Stroke Replay",
    "author": "AAADart & ChatGPT",
    "version": (0, 2, 0),
    "blender": (4, 2, 0),
    "location": "Sculpt Mode: Alt+Shift+R / 3D View > Sidebar > Tool",
    "description": "Replay and adjust the strength of the last sculpt stroke, including Multires (Alt+Shift+R).",
    "category": "Sculpt",
}

import bpy
from array import array


ADDON_KEYMAPS = []
THRESHOLD = 0.000001

# ---------------------------------------------------------------------------
# Shared preview state
# ---------------------------------------------------------------------------

PREVIEW_BACKEND = None  # None / "MESH" / "MULTIRES"

PREVIEW_OBJ_NAME = ""
PREVIEW_BASE_AFTER = None
PREVIEW_CACHE = None

# Multires-only preview state.
MR_MODIFIER_NAME = ""
MR_LEVEL = 0
MR_OLD_VIEWPORT_LEVEL = 0
MR_SOURCE_OBJ_NAME = ".SSR_MultiresPreviewSource"
MR_IN_UPDATE = False


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def active_mesh():
    obj = bpy.context.object
    return obj if obj and obj.type == 'MESH' else None


def get_multires(obj, preferred_name=None):
    if not obj:
        return None

    if preferred_name:
        mod = obj.modifiers.get(preferred_name)
        if mod and mod.type == 'MULTIRES':
            return mod

    for mod in obj.modifiers:
        if mod.type == 'MULTIRES':
            return mod

    return None


def redraw_viewports():
    screen = bpy.context.screen
    if not screen:
        return

    for area in screen.areas:
        if area.type == 'VIEW_3D':
            area.tag_redraw()


def get_coords(mesh):
    coords = array('f', [0.0]) * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", coords)
    return coords


def set_coords(mesh, coords):
    mesh.vertices.foreach_set("co", coords)
    mesh.update()
    redraw_viewports()


def build_cache(before, after, vert_count):
    cache = []
    threshold_sq = THRESHOLD * THRESHOLD

    for i in range(vert_count):
        j = i * 3

        ax = after[j]
        ay = after[j + 1]
        az = after[j + 2]

        dx = ax - before[j]
        dy = ay - before[j + 1]
        dz = az - before[j + 2]

        if dx * dx + dy * dy + dz * dz > threshold_sq:
            cache.append((i, ax, ay, az, dx, dy, dz))

    return cache


def mode_set_no_undo(mode):
    """
    The history-flush Multires backend relies on these bookkeeping mode
    switches NOT creating a new undo step.
    """
    return bpy.ops.object.mode_set('EXEC_DEFAULT', False, mode=mode)


def select_target_only(obj):
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        mode_set_no_undo('OBJECT')

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def reacquire_multires_target():
    obj = bpy.data.objects.get(PREVIEW_OBJ_NAME)
    if not obj:
        return None, None

    mr = get_multires(obj, MR_MODIFIER_NAME)
    return obj, mr


def remove_multires_source():
    source = bpy.data.objects.get(MR_SOURCE_OBJ_NAME)
    if not source:
        return

    mesh = source.data if source.type == 'MESH' else None
    bpy.data.objects.remove(source, do_unlink=True)

    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)



def unlink_multires_source_from_scene(source=None):
    """
    Keep the helper object as an unlinked bpy.data object while the dialog is
    open. It therefore does not remain in the Scene/Outliner between previews.
    """
    if source is None:
        source = bpy.data.objects.get(MR_SOURCE_OBJ_NAME)

    if not source:
        return

    for collection in list(source.users_collection):
        try:
            collection.objects.unlink(source)
        except Exception:
            pass


def link_multires_source_for_reshape(source, target):
    """
    multires_reshape needs the source to exist in the active view layer.
    Link it only for the instant in which Reshape runs.
    """
    unlink_multires_source_from_scene(source)

    if target.users_collection:
        collection = target.users_collection[0]
    else:
        collection = bpy.context.scene.collection

    collection.objects.link(source)


def restore_multires_sculpt_state():
    """
    Restore the user's normal Sculpt Mode immediately after every preview
    update. Object Mode is used only internally for a few operations.
    """
    obj, mr = reacquire_multires_target()

    if not obj or not mr:
        return

    if obj.mode != 'OBJECT':
        mode_set_no_undo('OBJECT')

    obj = bpy.data.objects.get(PREVIEW_OBJ_NAME)
    mr = get_multires(obj, MR_MODIFIER_NAME)

    if mr and mr.levels != MR_OLD_VIEWPORT_LEVEL:
        mr.levels = MR_OLD_VIEWPORT_LEVEL
        bpy.context.view_layer.update()

    bpy.ops.object.select_all(action='DESELECT')
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

    mode_set_no_undo('SCULPT')
    redraw_viewports()


def clear_preview_state():
    global PREVIEW_BACKEND
    global PREVIEW_OBJ_NAME, PREVIEW_BASE_AFTER, PREVIEW_CACHE
    global MR_MODIFIER_NAME, MR_LEVEL, MR_OLD_VIEWPORT_LEVEL, MR_IN_UPDATE

    PREVIEW_BACKEND = None

    PREVIEW_OBJ_NAME = ""
    PREVIEW_BASE_AFTER = None
    PREVIEW_CACHE = None

    MR_MODIFIER_NAME = ""
    MR_LEVEL = 0
    MR_OLD_VIEWPORT_LEVEL = 0
    MR_IN_UPDATE = False


# ---------------------------------------------------------------------------
# Normal mesh backend — original v0.1.6 behavior
# ---------------------------------------------------------------------------

def apply_mesh_preview_strength(strength):
    obj = bpy.data.objects.get(PREVIEW_OBJ_NAME)

    if not obj or PREVIEW_BASE_AFTER is None or not PREVIEW_CACHE:
        return

    coords = array('f', PREVIEW_BASE_AFTER)

    for i, ax, ay, az, dx, dy, dz in PREVIEW_CACHE:
        j = i * 3
        coords[j] = ax + dx * strength
        coords[j + 1] = ay + dy * strength
        coords[j + 2] = az + dz * strength

    set_coords(obj.data, coords)


def invoke_mesh_backend(operator):
    global PREVIEW_BACKEND
    global PREVIEW_OBJ_NAME, PREVIEW_BASE_AFTER, PREVIEW_CACHE

    obj = active_mesh()
    if not obj:
        operator.report({'WARNING'}, "Active object must be a mesh")
        return False

    mesh = obj.data
    vert_count = len(mesh.vertices)
    after = get_coords(mesh)

    bpy.ops.ed.undo()

    obj_before = active_mesh()
    if not obj_before:
        operator.report({'WARNING'}, "Last change was not a brushstroke")
        return False

    before = get_coords(obj_before.data)

    if len(before) != len(after):
        bpy.ops.ed.redo()
        operator.report({'WARNING'}, "Last change was not a brushstroke")
        return False

    bpy.ops.ed.redo()

    obj = active_mesh()
    if not obj:
        operator.report({'WARNING'}, "Redo changed active object")
        return False

    cache = build_cache(before, after, vert_count)

    if not cache:
        operator.report({'WARNING'}, "Last change was not a brushstroke")
        return False

    PREVIEW_BACKEND = "MESH"
    PREVIEW_OBJ_NAME = obj.name
    PREVIEW_BASE_AFTER = after
    PREVIEW_CACHE = cache

    return True


# ---------------------------------------------------------------------------
# Multires backend
# ---------------------------------------------------------------------------

def evaluated_coords(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()

    obj_eval = obj.evaluated_get(depsgraph)
    return get_coords(obj_eval.data)


def flush_multires_snapshot(obj_name, modifier_name, target_level):
    """
    Flush live Sculpt/PBVH Multires displacement into evaluated geometry.

    The crucial detail is that the mode switches are called with undo=False,
    which allows us to inspect the undone state and still keep Redo available.
    """
    obj = bpy.data.objects.get(obj_name)
    if not obj:
        raise RuntimeError("Multires object disappeared")

    mr = get_multires(obj, modifier_name)
    if not mr:
        raise RuntimeError("Multires modifier disappeared")

    if obj.mode != 'SCULPT':
        raise RuntimeError("Expected Sculpt Mode while capturing Multires stroke")

    old_viewport_level = mr.levels

    mode_set_no_undo('OBJECT')

    obj = bpy.data.objects.get(obj_name)
    mr = get_multires(obj, modifier_name)

    if target_level > mr.total_levels:
        raise RuntimeError("Multires level changed during history capture")

    if mr.levels != target_level:
        mr.levels = target_level
        bpy.context.view_layer.update()

    obj.update_tag(refresh={'DATA'})
    bpy.context.view_layer.update()

    coords = evaluated_coords(obj)

    if mr.levels != old_viewport_level:
        mr.levels = old_viewport_level
        bpy.context.view_layer.update()

    mode_set_no_undo('SCULPT')

    return coords


def create_multires_preview_source():
    """
    Create one full-resolution helper mesh from the evaluated AFTER surface.
    During slider movement we only edit vertices touched by the captured stroke;
    topology is not rebuilt for every slider update.
    """
    remove_multires_source()

    obj, mr = reacquire_multires_target()
    if not obj or not mr:
        raise RuntimeError("Multires target is unavailable")

    if obj.mode != 'OBJECT':
        mode_set_no_undo('OBJECT')

    if mr.levels != MR_LEVEL:
        mr.levels = MR_LEVEL
        bpy.context.view_layer.update()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    obj_eval = obj.evaluated_get(depsgraph)

    source_mesh = bpy.data.meshes.new_from_object(
        obj_eval,
        preserve_all_data_layers=False,
        depsgraph=depsgraph,
    )

    if len(source_mesh.vertices) * 3 != len(PREVIEW_BASE_AFTER):
        bpy.data.meshes.remove(source_mesh)
        raise RuntimeError(
            "Evaluated Multires preview mesh has an unexpected vertex count"
        )

    source = bpy.data.objects.new(MR_SOURCE_OBJ_NAME, source_mesh)
    source.matrix_world = obj.matrix_world.copy()
    source.hide_render = True
    source.display_type = 'WIRE'

    # Intentionally DO NOT link the helper to any collection here.
    # It lives only in bpy.data while the dialog is open, so it does not sit
    # in the Scene/Outliner. We link it for a few milliseconds during Reshape.


def apply_multires_preview_strength(strength):
    global MR_IN_UPDATE

    if MR_IN_UPDATE:
        return

    if PREVIEW_BASE_AFTER is None or not PREVIEW_CACHE:
        return

    obj, mr = reacquire_multires_target()
    source = bpy.data.objects.get(MR_SOURCE_OBJ_NAME)

    if not obj or not mr or not source:
        return

    MR_IN_UPDATE = True

    try:
        # The user remains in Sculpt Mode between updates. We only leave Sculpt
        # internally for the short Reshape operation.
        if obj.mode != 'OBJECT':
            mode_set_no_undo('OBJECT')

        obj, mr = reacquire_multires_target()

        if mr.levels != MR_LEVEL:
            mr.levels = MR_LEVEL
            bpy.context.view_layer.update()

        # Only vertices touched by the captured stroke need to be edited.
        verts = source.data.vertices

        for i, ax, ay, az, dx, dy, dz in PREVIEW_CACHE:
            verts[i].co = (
                ax + dx * strength,
                ay + dy * strength,
                az + dz * strength,
            )

        source.data.update()

        # The source is normally unlinked, hence invisible in the Scene/Outliner.
        # Link it only while Blender's Multires Reshape operator needs it.
        link_multires_source_for_reshape(source, obj)

        try:
            bpy.ops.object.select_all(action='DESELECT')
            source.select_set(True)
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

            result = bpy.ops.object.multires_reshape(modifier=mr.name)

            if 'FINISHED' not in result:
                raise RuntimeError(f"Multires Reshape returned {result}")

            bpy.context.view_layer.update()

        finally:
            # Immediately remove helper from the scene again.
            unlink_multires_source_from_scene(source)

        # Return to the exact user-facing state after every slider change.
        restore_multires_sculpt_state()

    finally:
        # Even if an exception occurs, do not leave the helper linked.
        try:
            unlink_multires_source_from_scene(source)
        except Exception:
            pass

        MR_IN_UPDATE = False


def finish_multires_preview():
    """
    Delete the internal helper datablock and ensure the user is left exactly
    where they started: target active, original viewport level, Sculpt Mode.
    """
    try:
        unlink_multires_source_from_scene()
        restore_multires_sculpt_state()
    finally:
        remove_multires_source()


def invoke_multires_backend(operator, obj, mr):
    global PREVIEW_BACKEND
    global PREVIEW_OBJ_NAME, PREVIEW_BASE_AFTER, PREVIEW_CACHE
    global MR_MODIFIER_NAME, MR_LEVEL, MR_OLD_VIEWPORT_LEVEL

    obj_name = obj.name
    modifier_name = mr.name

    # Capture this BEFORE doing any operator call. A Sculpt-mode subdivision can
    # make sculpt_levels higher than levels; the stroke belongs to sculpt_levels.
    level = mr.sculpt_levels
    old_viewport_level = mr.levels

    if level <= 0:
        return invoke_mesh_backend(operator)

    undo_done = False
    redo_done = False

    try:
        # IMPORTANT: Undo is the first operator executed here. Doing a mode
        # switch before it would make Undo hit the wrong history step.
        if not bpy.ops.ed.undo.poll():
            operator.report({'WARNING'}, "Last change was not a brushstroke")
            return False

        bpy.ops.ed.undo()
        undo_done = True

        before = flush_multires_snapshot(
            obj_name,
            modifier_name,
            level,
        )

        if not bpy.ops.ed.redo.poll():
            operator.report({'WARNING'}, "Could not restore the sculpt stroke")
            return False

        bpy.ops.ed.redo()
        redo_done = True

        after = flush_multires_snapshot(
            obj_name,
            modifier_name,
            level,
        )

        if len(before) != len(after):
            operator.report({'WARNING'}, "Last change was not a brushstroke")
            return False

        vert_count = len(after) // 3
        cache = build_cache(before, after, vert_count)

        if not cache:
            operator.report({'WARNING'}, "Last change was not a brushstroke")
            return False

        obj = bpy.data.objects.get(obj_name)
        mr = get_multires(obj, modifier_name)

        if not obj or not mr:
            operator.report({'WARNING'}, "Redo changed the Multires object")
            return False

        PREVIEW_BACKEND = "MULTIRES"
        PREVIEW_OBJ_NAME = obj_name
        PREVIEW_BASE_AFTER = after
        PREVIEW_CACHE = cache

        MR_MODIFIER_NAME = modifier_name
        MR_LEVEL = level
        MR_OLD_VIEWPORT_LEVEL = old_viewport_level

        # Build the full-resolution helper once. It remains UNLINKED from the
        # scene while the dialog is open.
        if obj.mode != 'OBJECT':
            mode_set_no_undo('OBJECT')

        if mr.levels != level:
            mr.levels = level
            bpy.context.view_layer.update()

        create_multires_preview_source()

        # Crucial UX change: return to Sculpt Mode BEFORE showing the slider.
        restore_multires_sculpt_state()

        return True

    except Exception as exc:
        # If capture failed after Undo but before successful Redo, make a best
        # effort to restore the user's stroke.
        if undo_done and not redo_done:
            try:
                if bpy.ops.ed.redo.poll():
                    bpy.ops.ed.redo()
            except Exception:
                pass

        unlink_multires_source_from_scene()
        remove_multires_source()

        try:
            obj = bpy.data.objects.get(obj_name)
            mr = get_multires(obj, modifier_name) if obj else None

            if obj and mr:
                PREVIEW_OBJ_NAME = obj_name
                MR_MODIFIER_NAME = modifier_name
                MR_OLD_VIEWPORT_LEVEL = old_viewport_level
                restore_multires_sculpt_state()
        except Exception:
            pass

        operator.report({'WARNING'}, f"Multires replay failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Unified preview callback
# ---------------------------------------------------------------------------

def apply_preview_strength(strength):
    if PREVIEW_BACKEND == "MULTIRES":
        apply_multires_preview_strength(strength)
    elif PREVIEW_BACKEND == "MESH":
        apply_mesh_preview_strength(strength)


def live_update_strength(self, context):
    apply_preview_strength(self.strength)


# ---------------------------------------------------------------------------
# Main operator — same user-facing UX as v0.1.6
# ---------------------------------------------------------------------------

class SCULPT_OT_live_amplify_last_stroke(bpy.types.Operator):
    bl_idname = "sculpt.live_amplify_last_stroke"
    bl_label = "Replay Last Sculpt Stroke"
    bl_options = {'UNDO'}

    strength: bpy.props.FloatProperty(
        name="Repeat Strength",
        default=1.0,
        min=-100.0,
        max=100.0,
        soft_min=-5.0,
        soft_max=5.0,
        step=10,
        precision=2,
        update=live_update_strength,
        description="1.0 = replay once, 0.5 = half, -0.5 = reduce, 2.0 = double",
    )

    @classmethod
    def poll(cls, context):
        return (
            context.mode == 'SCULPT'
            and context.object
            and context.object.type == 'MESH'
        )

    def draw(self, context):
        # Intentionally identical to the old add-on UX.
        self.layout.prop(self, "strength", slider=True)

    def invoke(self, context, event):
        clear_preview_state()
        remove_multires_source()

        obj = active_mesh()

        if not obj:
            self.report({'WARNING'}, "Active object must be a mesh")
            return {'CANCELLED'}

        mr = get_multires(obj)

        # Use Multires history-flush backend only when actual Multires levels
        # exist. Plain sculpt meshes keep the original v0.1.6 path.
        if mr and mr.total_levels > 0 and mr.show_viewport:
            ok = invoke_multires_backend(self, obj, mr)
        else:
            ok = invoke_mesh_backend(self)

        if not ok:
            clear_preview_state()
            return {'CANCELLED'}

        # Same behavior as v0.1.6: opening the dialog starts at one extra replay.
        self.strength = 1.0
        apply_preview_strength(1.0)

        return context.window_manager.invoke_props_dialog(self, width=360)

    def execute(self, context):
        try:
            apply_preview_strength(self.strength)

            if PREVIEW_BACKEND == "MULTIRES":
                finish_multires_preview()

            self.report({'INFO'}, f"Repeat Strength: {self.strength:.2f}")
            return {'FINISHED'}

        finally:
            clear_preview_state()

    def cancel(self, context):
        try:
            # Strength 0.0 means exactly the original AFTER-stroke surface.
            apply_preview_strength(0.0)

            if PREVIEW_BACKEND == "MULTIRES":
                finish_multires_preview()

            self.report({'INFO'}, "Sculpt Stroke Replay cancelled")

        finally:
            clear_preview_state()


# ---------------------------------------------------------------------------
# Existing sidebar UI
# ---------------------------------------------------------------------------

class VIEW3D_PT_sculpt_stroke_replay_panel(bpy.types.Panel):
    bl_label = "Sculpt Stroke Replay"
    bl_idname = "VIEW3D_PT_sculpt_stroke_replay_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"

    @classmethod
    def poll(cls, context):
        return context.mode == 'SCULPT'

    def draw(self, context):
        layout = self.layout
        layout.label(text="After a sculpt stroke:")
        layout.label(text="Alt + Shift + R")
        layout.operator(
            "sculpt.live_amplify_last_stroke",
            text="Replay Last Stroke",
        )


classes = (
    SCULPT_OT_live_amplify_last_stroke,
    VIEW3D_PT_sculpt_stroke_replay_panel,
)


def register():
    global ADDON_KEYMAPS

    for cls in classes:
        bpy.utils.register_class(cls)

    kc = bpy.context.window_manager.keyconfigs.addon

    if kc:
        km = kc.keymaps.new(name="Sculpt", space_type="EMPTY")
        kmi = km.keymap_items.new(
            "sculpt.live_amplify_last_stroke",
            type="R",
            value="PRESS",
            alt=True,
            shift=True,
        )
        ADDON_KEYMAPS.append((km, kmi))


def unregister():
    global ADDON_KEYMAPS

    # Remove a helper left behind by a crash/interrupted dialog if possible.
    remove_multires_source()

    for km, kmi in ADDON_KEYMAPS:
        km.keymap_items.remove(kmi)

    ADDON_KEYMAPS.clear()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
