"""Alepou Blender Bridge extension entry point."""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import alepou_discovery, protocol, service, spatial_policy

bl_info = {
    "name": "Alepou Blender Bridge",
    "author": "Alepou",
    "version": (0, 3, 0),
    "blender": (4, 1, 0),
    "location": "View3D > Sidebar > Alepou",
    "description": "Auditable local bridge for Alepou-managed AI sessions",
    "category": "Development",
}


def _restart(_self: object, _context: object) -> None:
    service.get_service().start()


def _authority_changed(self: object, _context: object) -> None:
    bridge = service.get_service()
    bridge.authority_initialized = True
    bridge.session_trust_mode = self.trust_mode
    bridge.session_id = str(self.session_id or "").strip() or None
    bridge.start()


def _binding_changed(self: object, _context: object) -> None:
    bridge = service.get_service()
    next_root = bridge.project_root()
    selected = alepou_discovery.project_by_id(str(getattr(self, "project_id", "") or ""))
    if selected and next_root is not None:
        try:
            selected_root = protocol.canonical_project_root(selected["path"])
        except protocol.ProtocolError:
            selected_root = None
        if selected_root != next_root:
            self.project_id = ""
    if bridge._known_project is not None and next_root != bridge._known_project:
        bridge.session_trust_mode = "observation"
        bridge.session_id = None
        self.trust_mode = "observation"
        self.session_id = ""
    bridge.start()


def _project_items(self: object, context: object) -> list[tuple[str, str, str]]:
    return alepou_discovery.enum_items(self, context)


class ALEPOU_Preferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    project_root: StringProperty(
        name="Alepou Project Root",
        description="Explicit project root; bridge files live under plan/blender",
        subtype="DIR_PATH",
        update=_binding_changed,
    )
    project_id: StringProperty(
        name="Alepou Project ID",
        description="Stable Alepou project identity paired with the canonical project root",
        default="",
        update=_binding_changed,
    )
    discovered_project: EnumProperty(
        name="Open Alepou Project",
        description="Project advertised by the authenticated local Alepou application",
        items=_project_items,
    )
    discovery_status: StringProperty(
        name="Discovery Status",
        default="Refresh to discover projects from Alepou",
    )
    processor_enabled: BoolProperty(
        name="Enable Processing",
        description="Process observation and authorized command files while Blender is open",
        default=True,
        update=_restart,
    )
    trust_mode: EnumProperty(
        name="Authority",
        description="Observation is automatic; Local Trusted Development records and permits local scene mutation",
        items=(
            ("observation", "Observation Only", "Permit bounded state queries and captures"),
            ("trusted_development", "Local Trusted Development", "Permit audited scripts and local scene mutations until revoked or Blender closes"),
        ),
        default="observation",
        update=_authority_changed,
    )
    session_id: StringProperty(
        name="Owning Alepou Session",
        description="Optional exact session id required on mutation requests",
        default="",
        update=_authority_changed,
    )
    allow_external_save_paths: BoolProperty(
        name="Allow External Save Paths",
        description="Allow explicit save-copy destinations outside the bound project",
        default=False,
    )

    def draw(self, _context: object) -> None:
        layout = self.layout
        layout.operator("alepou.bridge_refresh_projects", icon="FILE_REFRESH")
        layout.prop(self, "discovered_project")
        selected = alepou_discovery.project_by_id(self.discovered_project)
        if selected:
            layout.label(text=f"Selected root: {selected['path']}")
            layout.label(
                text=(
                    f"Active: {selected['runningSessionCount']} Alepou session(s), "
                    f"{selected['connectedInstanceCount']} Blender instance(s)"
                )
            )
        layout.operator("alepou.bridge_bind_project", icon="LINKED")
        layout.label(text=self.discovery_status)
        layout.prop(self, "project_root")
        layout.prop(self, "processor_enabled")
        layout.prop(self, "trust_mode")
        layout.prop(self, "session_id")
        layout.prop(self, "allow_external_save_paths")
        _draw_spatial_controls(layout, service.get_service())
        layout.operator("alepou.bridge_export_state", icon="FILE_REFRESH")
        layout.operator("alepou.bridge_stop", icon="CANCEL")


class ALEPOU_OT_RefreshProjects(bpy.types.Operator):
    bl_idname = "alepou.bridge_refresh_projects"
    bl_label = "Refresh Alepou Projects"
    bl_description = "Read the authenticated local Alepou project catalogue"

    def execute(self, context: object) -> set[str]:
        preferences = context.preferences.addons[__package__].preferences
        try:
            projects = alepou_discovery.refresh_projects()
        except alepou_discovery.AlepouDiscoveryError as error:
            preferences.discovery_status = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        selected = alepou_discovery.project_by_id(preferences.discovered_project)
        if selected is None and projects:
            preferences.discovered_project = projects[0]["projectId"]
        preferences.discovery_status = (
            f"Found {len(projects)} Alepou project(s)"
            if projects
            else "Alepou is running, but it has no registered projects"
        )
        self.report({"INFO"}, preferences.discovery_status)
        return {"FINISHED"}


class ALEPOU_OT_BindProject(bpy.types.Operator):
    bl_idname = "alepou.bridge_bind_project"
    bl_label = "Bind and Initialize"
    bl_description = "Bind this Blender instance to the selected project and publish fresh bridge state"

    def execute(self, context: object) -> set[str]:
        preferences = context.preferences.addons[__package__].preferences
        project = alepou_discovery.project_by_id(preferences.discovered_project)
        if project is None:
            self.report({"ERROR"}, "Refresh Alepou projects and select one first")
            return {"CANCELLED"}
        if not project["bindable"]:
            self.report({"ERROR"}, "This Alepou project has explicitly opted out of bridge binding")
            return {"CANCELLED"}
        try:
            project_root = protocol.canonical_project_root(project["path"])
        except protocol.ProtocolError as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

        bridge = service.get_service()
        preferences.trust_mode = "observation"
        preferences.session_id = ""
        preferences.project_root = str(project_root)
        preferences.project_id = project["projectId"]
        preferences.processor_enabled = True
        bridge.authority_initialized = True
        bridge.session_trust_mode = "observation"
        bridge.session_id = None
        bridge.start()
        root = bridge.root()
        if root is None:
            self.report({"ERROR"}, "The selected project could not be initialized")
            return {"CANCELLED"}
        bridge.export_state(root, reason="alepou_project_binding")
        bridge.write_health(root)
        preferences.discovery_status = f"Bound to {project['name']} in Observation Only mode"
        self.report({"INFO"}, preferences.discovery_status)
        return {"FINISHED"}


class ALEPOU_OT_ExportState(bpy.types.Operator):
    bl_idname = "alepou.bridge_export_state"
    bl_label = "Export Blender Bridge State"
    bl_description = "Write fresh bounded Blender state to the bound project"

    def execute(self, _context: object) -> set[str]:
        bridge = service.get_service()
        root = bridge.root()
        if root is None:
            self.report({"ERROR"}, "Bind an existing Alepou project root first")
            return {"CANCELLED"}
        bridge.export_state(root, reason="manual")
        bridge.write_health(root)
        self.report({"INFO"}, f"Exported Blender Bridge state to {root}")
        return {"FINISHED"}


class ALEPOU_OT_Stop(bpy.types.Operator):
    bl_idname = "alepou.bridge_stop"
    bl_label = "STOP Bridge"
    bl_description = "Block new claims immediately; already-executing Python cannot be forcibly stopped safely"

    def execute(self, context: object) -> set[str]:
        preferences = context.preferences.addons[__package__].preferences
        preferences.processor_enabled = False
        service.get_service().stop("stopped_by_user")
        self.report({"WARNING"}, "Alepou Blender Bridge stopped")
        return {"FINISHED"}


class ALEPOU_OT_SetSpatialMode(bpy.types.Operator):
    bl_idname = "alepou.bridge_set_spatial_mode"
    bl_label = "Set Spatial Mode"
    bl_description = "Set the explicit project-level Spatial representation policy"

    mode: EnumProperty(
        name="Spatial Mode",
        items=(
            ("off", "Off", "Raw bpy only; reject Spatial authoring"),
            ("opt_in", "Opt In", "Allow raw bpy or explicitly selected Spatial authoring"),
            ("required", "Required", "Require Spatial and reject raw bpy authoring"),
        ),
        default="off",
    )

    def execute(self, _context: object) -> set[str]:
        bridge = service.get_service()
        root = bridge.policy_root()
        if root is None:
            self.report({"ERROR"}, "Bind an existing Alepou project root first")
            return {"CANCELLED"}
        protocol.atomic_write_json(
            spatial_policy.policy_path(root),
            {"schemaVersion": 1, "mode": self.mode, "fallbackAllowed": False},
        )
        bridge.export_capabilities(root)
        bridge.write_health(root)
        self.report({"INFO"}, f"Spatial mode: {self.mode.replace('_', ' ')}")
        return {"FINISHED"}


def _draw_spatial_controls(layout: object, bridge: object) -> None:
    root = bridge.policy_root()
    try:
        mode = spatial_policy.read_mode(root) if root else "off"
    except spatial_policy.SpatialPolicyError:
        mode = "invalid"
    layout.label(text=f"Spatial: {mode.replace('_', ' ').title()}")
    row = layout.row(align=True)
    for value, label in (("off", "Off"), ("opt_in", "Opt In"), ("required", "Required")):
        operator = row.operator("alepou.bridge_set_spatial_mode", text=label, depress=mode == value)
        operator.mode = value


class ALEPOU_PT_Bridge(bpy.types.Panel):
    bl_label = "Alepou Blender Bridge"
    bl_idname = "ALEPOU_PT_blender_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Alepou"

    def draw(self, context: object) -> None:
        layout = self.layout
        preferences = context.preferences.addons[__package__].preferences
        bridge = service.get_service()
        layout.label(text=f"Authority: {preferences.trust_mode.replace('_', ' ').title()}")
        project_box = layout.box()
        project_box.label(text="Alepou Project")
        row = project_box.row(align=True)
        row.prop(preferences, "discovered_project", text="")
        row.operator("alepou.bridge_refresh_projects", text="", icon="FILE_REFRESH")
        project_box.operator("alepou.bridge_bind_project", icon="LINKED")
        project_box.label(text=preferences.discovery_status)
        selected = alepou_discovery.project_by_id(preferences.discovered_project)
        if selected:
            project_box.label(text=f"Selected root: {selected['path']}")
            project_box.label(
                text=(
                    f"Active: {selected['runningSessionCount']} Alepou session(s), "
                    f"{selected['connectedInstanceCount']} Blender instance(s)"
                )
            )
        project_box.label(text=f"ID: {preferences.project_id or '(manual binding)'}")
        actual_root = bridge.project_root()
        project_box.label(text=f"Root: {actual_root or '(not bound)'}")
        project_box.prop(preferences, "project_root", text="Manual Folder")
        layout.label(text=f"Instance: {bridge.instance_id}")
        layout.prop(preferences, "processor_enabled", text="Processing")
        _draw_spatial_controls(layout, bridge)
        layout.operator("alepou.bridge_export_state", icon="FILE_REFRESH")
        layout.operator("alepou.bridge_stop", icon="CANCEL")


CLASSES = (
    ALEPOU_Preferences,
    ALEPOU_OT_RefreshProjects,
    ALEPOU_OT_BindProject,
    ALEPOU_OT_ExportState,
    ALEPOU_OT_Stop,
    ALEPOU_OT_SetSpatialMode,
    ALEPOU_PT_Bridge,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    service.install()


def unregister() -> None:
    service.uninstall()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
