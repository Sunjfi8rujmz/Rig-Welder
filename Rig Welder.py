bl_info = {
    'name': 'Weld Parts',
    'version': (1, 1, 0),
    'blender': (4, 0, 0),
    'location': 'View3D > Sidebar > Weld',
    'description': "Weld imported rig parts to the animated rig's Internal armature",
    'category': 'Animation',
}

import re
import bpy
from mathutils import Vector

HEAD_WORDS = ('head', 'hat', 'hair', 'mask', 'face', 'helmet', 'hood', 'neck', 'glasses', 'horn', 'crown')
TORSO_WORDS = ('torso', 'chest', 'spine', 'waist', 'pelvis', 'hip', 'belt', 'scarf', 'shirt', 'vest',
               'harness', 'cape', 'backpack')
ARM_WORDS = ('arm', 'hand', 'shoulder', 'elbow', 'wrist', 'glove', 'sleeve')
LEG_WORDS = ('leg', 'foot', 'feet', 'knee', 'thigh', 'shin', 'shoe', 'boot', 'ankle')
FAR_LIMIT = 8.0

_ICONS = None

def icon(*names, default='DOT'):
    global _ICONS
    if _ICONS is None:
        try:
            _ICONS = set(bpy.types.UILayout.bl_rna.functions['label'].parameters['icon'].enum_items.keys())
        except Exception:
            _ICONS = set()
    for n in names:
        if n in _ICONS:
            return n
    return default

def norm(s):
    return re.sub('[^a-z0-9]', '', s.lower())

def tokens(s):
    return [t.lower() for t in re.findall('[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\\d+', s)]

def bone_chain(arm, bone_name):
    out = []
    b = arm.data.bones.get(bone_name)
    while b:
        out.append(b.name)
        b = b.parent
    return out

def fuzzy(name, bones):
    k = norm(name)
    t = tokens(name)
    side = 'left' if ('left' in k or 'l' in t) else 'right' if ('right' in k or 'r' in t) else None
    if any(w in k for w in HEAD_WORDS):
        target = 'head'
    elif any(w in k for w in TORSO_WORDS):
        target = 'torso'
    elif side and any(w in k for w in ARM_WORDS):
        target = side + 'arm'
    elif side and any(w in k for w in LEG_WORDS):
        target = side + 'leg'
    else:
        return None
    return bones.get(target)

def suggest(part, internal):
    bones = {norm(b.name): b.name for b in internal.data.bones}
    chains = []
    for c in part.constraints:
        if (c.type == 'CHILD_OF' and c.target and c.target.type == 'ARMATURE'
                and c.subtarget and c.target != internal):
            chains.append(bone_chain(c.target, c.subtarget))
    if (part.parent and part.parent.type == 'ARMATURE' and part.parent_type == 'BONE'
            and part.parent_bone and part.parent != internal):
        chains.append(bone_chain(part.parent, part.parent_bone))
    chains.append([re.sub('\\.\\d+$', '', part.name)])

    guess = None
    for chain in chains:
        for lvl, name in enumerate(chain):
            k = norm(name)
            if k in bones and not (lvl > 0 and k == 'humanoidrootpart'):
                return bones[k], 'READY', ('' if lvl == 0 else 'via ' + name)
            if guess is None:
                f = fuzzy(name, bones)
                if f:
                    guess = (f, 'similar to ' + name)
    if guess:
        return guess[0], 'CONFIRM', guess[1]
    return '', 'CONFIRM', 'no match'

def bbox_center(o):
    pts = [o.matrix_world @ Vector(c) for c in o.bound_box]
    return sum(pts, Vector()) / len(pts)

def far_from_bone(part, arm, bone_name):
    b = arm.data.bones.get(bone_name)
    if not b:
        return None
    d = (bbox_center(part) - (arm.matrix_world @ b.head_local)).length
    return d if d > FAR_LIMIT else None

def parents_of(col, sc):
    res = [c for c in bpy.data.collections if col.name in c.children]
    if col.name in sc.collection.children:
        res.append(sc.collection)
    return res

def find_lc(lc, col):
    if lc.collection == col:
        return lc
    for ch in lc.children:
        r = find_lc(ch, col)
        if r:
            return r
    return None

def is_internal(o):
    return 'internal' in o.name.lower() or any('internal' in c.name.lower() for c in o.users_collection)

def drives(arm, other):
    for pb in arm.pose.bones:
        for c in pb.constraints:
            if getattr(c, 'target', None) == other:
                return True
    return False

def resolve(sc):
    pool = []
    if sc.weld_rig and sc.weld_rig.type == 'ARMATURE':
        pool.append(sc.weld_rig)
    if sc.weld_rig_col:
        pool += [o for o in sc.weld_rig_col.all_objects if o.type == 'ARMATURE' and o not in pool]
    allarms = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    internal = next((o for o in pool if is_internal(o)), None)
    if internal is None and pool:
        for p in pool:
            internal = next((a for a in allarms if a != p and drives(a, p)), None)
            if internal:
                break
        if internal is None:
            internal = pool[0]
    if internal is None and not pool:
        internal = next((a for a in allarms if is_internal(a)), None)
    if internal is None:
        return None, None, None

    primary = next((p for p in pool if p != internal), None)
    if primary is None:
        for pb in internal.pose.bones:
            for c in pb.constraints:
                t = getattr(c, 'target', None)
                if t and t.type == 'ARMATURE' and t != internal:
                    primary = t
                    break
            if primary:
                break

    rig_col = sc.weld_rig_col
    if rig_col is None and primary and primary.users_collection:
        c = primary.users_collection[0]
        rig_col = c if c != sc.collection else None
    if rig_col is None and internal.users_collection:
        ps = parents_of(internal.users_collection[0], sc)
        rig_col = ps[0] if ps and ps[0] != sc.collection else None
    return internal, primary, rig_col

def weld_item(item, internal):
    part = item.obj
    if part is None:
        return False, 'missing object'
    bone = internal.data.bones.get(item.bone)
    if bone is None:
        return False, 'pick a bone'
    if any(m.type == 'ARMATURE' for m in part.modifiers):
        return False, 'deforming mesh'

    old_welds = [c for c in part.constraints if c.type == 'CHILD_OF' and c.name.startswith('Weld_')]
    world = part.matrix_basis.copy() if old_welds else part.matrix_world.copy()

    for c in list(part.constraints):
        if c.type == 'CHILD_OF':
            part.constraints.remove(c)
    if part.parent is not None:
        part.parent = None
    part.matrix_world = world

    con = part.constraints.new('CHILD_OF')
    con.name = 'Weld_' + bone.name
    con.target = internal
    con.subtarget = bone.name

    con.inverse_matrix = (internal.matrix_world @ bone.matrix_local).inverted()
    return True, ''

def maybe_finish(sc):
    items = sc.weld_items
    if len(items) == 0 or any(i.state != 'WELDED' for i in items):
        return
    try:
        bpy.ops.weld.finish('INVOKE_DEFAULT')
    except Exception:
        pass

class WeldItem(bpy.types.PropertyGroup):
    obj: bpy.props.PointerProperty(type=bpy.types.Object)
    bone: bpy.props.StringProperty()
    state: bpy.props.StringProperty(default='CONFIRM')
    note: bpy.props.StringProperty()
    use: bpy.props.BoolProperty(default=False)

def _poll_armature(self, o):
    return o.type == 'ARMATURE'

class WELD_OT_scan(bpy.types.Operator):
    bl_idname = 'weld.scan'
    bl_label = 'Scan'

    def execute(self, context):
        sc = context.scene
        internal, primary, rig_col = resolve(sc)
        if internal is None:
            self.report({'ERROR'}, 'Pick the animated rig')
            return {'CANCELLED'}
        pc = sc.weld_parts_col
        if pc is None:
            self.report({'ERROR'}, 'Pick the parts collection')
            return {'CANCELLED'}
        context.view_layer.update()

        ps = parents_of(pc, sc)
        if ps and ps[0] != sc.collection and (rig_col is None or ps[0].name != rig_col.name):
            sc.weld_old_parent = ps[0].name

        sc.weld_items.clear()
        objs = sorted((o for o in pc.all_objects if o.type != 'ARMATURE'), key=lambda o: o.name)
        for o in objs:
            it = sc.weld_items.add()
            it.obj = o
            weld = next((c for c in o.constraints
                         if c.type == 'CHILD_OF' and c.name.startswith('Weld_') and c.target == internal), None)
            if weld:
                it.bone, it.state, it.note, it.use = weld.subtarget, 'WELDED', '', False
                continue
            bone, state, note = suggest(o, internal)
            if state == 'READY' and bone:
                d = far_from_bone(o, internal, bone)
                if d:
                    state, note = 'CONFIRM', '%.0f away from %s' % (d, bone)
            it.bone, it.state, it.note = bone, state, note
            it.use = (state == 'READY')
        return {'FINISHED'}

class WELD_OT_weld(bpy.types.Operator):
    bl_idname = 'weld.weld'
    bl_label = 'Weld'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        sc = context.scene
        internal, _p, _r = resolve(sc)
        if internal is None:
            self.report({'ERROR'}, 'Pick the animated rig')
            return {'CANCELLED'}
        context.view_layer.update()
        done = 0
        problems = 0
        for it in sc.weld_items:
            if not it.use or it.state == 'WELDED':
                continue
            ok, note = weld_item(it, internal)
            if ok:
                it.state, it.note, it.use = 'WELDED', '', False
                done += 1
            else:
                it.state = 'CONFIRM' if note == 'pick a bone' else 'FAILED'
                it.note = note
                problems += 1
        if done == 0 and problems == 0:
            self.report({'INFO'}, 'Nothing ticked')
            return {'CANCELLED'}
        self.report({'INFO'}, 'Welded %d' % done + (', %d need attention' % problems if problems else ''))
        if done:
            maybe_finish(sc)
        return {'FINISHED'}

class WELD_OT_select(bpy.types.Operator):
    bl_idname = 'weld.select'
    bl_label = 'Select'
    mode: bpy.props.StringProperty(default='ALL')

    def execute(self, context):
        for it in context.scene.weld_items:
            if it.state == 'WELDED':
                continue
            it.use = (self.mode == 'ALL') and bool(it.bone)
        return {'FINISHED'}

class WELD_OT_blank(bpy.types.Operator):
    bl_idname = 'weld.blank'
    bl_label = 'Blank Pose'

    def execute(self, context):
        internal, primary, _r = resolve(context.scene)
        if internal is None:
            self.report({'ERROR'}, 'Pick the animated rig')
            return {'CANCELLED'}
        to_rest = internal.data.pose_position != 'REST'
        for a in (internal, primary):
            if a is not None:
                a.data.pose_position = 'REST' if to_rest else 'POSE'
        return {'FINISHED'}

class WELD_OT_unweld(bpy.types.Operator):
    bl_idname = 'weld.unweld'
    bl_label = 'Unweld All'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        for it in context.scene.weld_items:
            if it.state != 'WELDED' or it.obj is None:
                continue
            for c in list(it.obj.constraints):
                if c.type == 'CHILD_OF' and c.name.startswith('Weld_'):
                    it.obj.constraints.remove(c)
            it.state, it.use = 'READY', True
        return {'FINISHED'}

def do_move(sc, parts_col, rig_col):
    dest = rig_col if rig_col else sc.collection
    if dest.name == parts_col.name:
        return 'parts not moved'
    if dest.name in {c.name for c in parts_col.children_recursive}:
        return 'parts not moved'
    ps = parents_of(parts_col, sc)
    if dest.name not in {p.name for p in ps}:
        dest.children.link(parts_col)
    for p in ps:
        if p.name != dest.name:
            p.children.unlink(parts_col)
    return 'parts moved to ' + dest.name

def do_hide(context, rig_col):
    pool = list(rig_col.children_recursive) if rig_col else []
    pool += list(bpy.data.collections)
    target = next((c for c in pool if c.name.startswith('BodyTypes')), None)
    if target is None:
        return 'BodyTypes not found'
    lc = find_lc(context.view_layer.layer_collection, target)
    if lc:
        lc.hide_viewport = True
    target.hide_render = True
    return 'BodyTypes hidden'

def do_delete(sc, parts_col, protect):
    old = bpy.data.collections.get(sc.weld_old_parent) if sc.weld_old_parent else None
    if old is None:
        return 'nothing to delete'
    tree = [old] + list(old.children_recursive)
    names = {c.name for c in tree}
    if parts_col and parts_col.name in names:
        return 'not deleted (parts are still inside it)'
    if any(i.state != 'WELDED' for i in sc.weld_items):
        return 'not deleted (not everything is welded)'
    objs = list(old.all_objects)
    if any(o.name in protect for o in objs):
        return 'not deleted (it contains the animated rig)'
    for o in objs:
        if all(c.name in names for c in o.users_collection):
            bpy.data.objects.remove(o, do_unlink=True)
    for c in tree:
        bpy.data.collections.remove(c)
    sc.weld_old_parent = ''
    return 'imported rig deleted'

class WELD_OT_finish(bpy.types.Operator):
    bl_idname = 'weld.finish'
    bl_label = 'Finish'
    bl_options = {'REGISTER', 'UNDO'}

    move_parts: bpy.props.BoolProperty(name='Move parts into rig collection', default=True)
    hide_body: bpy.props.BoolProperty(name='Hide BodyTypes', default=True)
    delete_old: bpy.props.BoolProperty(name='Delete imported rig', default=False)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        lay = self.layout
        lay.prop(self, 'move_parts')
        lay.prop(self, 'hide_body')
        lay.prop(self, 'delete_old')
        old = bpy.data.collections.get(context.scene.weld_old_parent)
        if self.delete_old and old:
            r = lay.row()
            r.alert = True
            r.label(text="Deletes '%s' and its contents" % old.name, icon='TRASH')

    def execute(self, context):
        sc = context.scene
        internal, primary, rig_col = resolve(sc)
        pc = sc.weld_parts_col
        msgs = []
        if self.move_parts and pc:
            msgs.append(do_move(sc, pc, rig_col))
        if self.hide_body:
            msgs.append(do_hide(context, rig_col))
        if self.delete_old:
            protect = {a.name for a in (internal, primary) if a}
            msgs.append(do_delete(sc, pc, protect))
        if msgs:
            self.report({'INFO'}, '; '.join(msgs))
        return {'FINISHED'}

class WELD_PT_main(bpy.types.Panel):
    bl_label = 'Weld Parts'
    bl_idname = 'WELD_PT_main'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Weld'

    def draw(self, context):
        sc = context.scene
        lay = self.layout
        col = lay.column(align=True)
        col.prop(sc, 'weld_rig', text='Rig')
        col.prop(sc, 'weld_rig_col', text='Rig Coll.')
        col.prop(sc, 'weld_parts_col', text='Parts')

        internal, _p, _r = resolve(sc)
        row = lay.row()
        if internal:
            row.label(text=internal.name, icon='ARMATURE_DATA')
        else:
            row.alert = True
            row.label(text='No rig found', icon='ERROR')

        row = lay.row(align=True)
        row.operator('weld.scan', icon='VIEWZOOM')
        row.operator('weld.weld', icon='CONSTRAINT')
        lay.operator('weld.blank', icon='POSE_HLT',
                     depress=bool(internal and internal.data.pose_position == 'REST'))

        items = sc.weld_items
        if internal is None or len(items) == 0:
            return

        todo = sum(1 for i in items if i.use and i.state != 'WELDED')
        ask = sum(1 for i in items if i.state == 'CONFIRM' and not i.use)
        done = sum(1 for i in items if i.state == 'WELDED')
        lay.label(text='%d to weld   %d to confirm   %d welded' % (todo, ask, done))
        row = lay.row(align=True)
        op = row.operator('weld.select', text='Select All')
        op.mode = 'ALL'
        op = row.operator('weld.select', text='None')
        op.mode = 'NONE'

        green = icon('STRIP_COLOR_04', 'SEQUENCE_COLOR_04', default='CHECKMARK')
        yellow = icon('STRIP_COLOR_03', 'SEQUENCE_COLOR_03', default='QUESTION')
        red = icon('STRIP_COLOR_01', 'SEQUENCE_COLOR_01', default='ERROR')
        icons = {'READY': green, 'CONFIRM': yellow, 'WELDED': 'CHECKMARK', 'FAILED': red}

        col = lay.column(align=True)
        for it in items:
            row = col.row(align=True)
            sp = row.split(factor=0.42, align=True)
            sp.label(text=it.obj.name if it.obj else '?', icon=icons.get(it.state, 'DOT'))
            r = sp.row(align=True)
            if it.state == 'WELDED':
                r.active = False
                r.label(text=it.bone)
            else:
                r.alert = it.state == 'FAILED'
                r.prop_search(it, 'bone', internal.data, 'bones', text='')
                r.prop(it, 'use', text='')
                if it.note and it.state != 'READY':
                    n = col.row()
                    n.alert = it.state == 'FAILED'
                    n.label(text='      ' + it.note)

        row = lay.row(align=True)
        row.operator('weld.finish', text='Finish...')
        row.operator('weld.unweld', text='Unweld All')

CLASSES = (WeldItem, WELD_OT_scan, WELD_OT_weld, WELD_OT_select, WELD_OT_blank,
           WELD_OT_unweld, WELD_OT_finish, WELD_PT_main)

def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    S = bpy.types.Scene
    S.weld_rig = bpy.props.PointerProperty(type=bpy.types.Object, poll=_poll_armature)
    S.weld_rig_col = bpy.props.PointerProperty(type=bpy.types.Collection)
    S.weld_parts_col = bpy.props.PointerProperty(type=bpy.types.Collection)
    S.weld_items = bpy.props.CollectionProperty(type=WeldItem)
    S.weld_old_parent = bpy.props.StringProperty()

def unregister():
    S = bpy.types.Scene
    for name in ('weld_rig', 'weld_rig_col', 'weld_parts_col', 'weld_items', 'weld_old_parent'):
        try:
            delattr(S, name)
        except Exception:
            pass
    for c in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(c)
        except Exception:
            pass

if __name__ == '__main__':
    unregister()
    register()
