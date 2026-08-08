"""Versioned workflow manifests and canonical frame selection."""

from .collection import (
    COLLECTION_DIRECTORY,
    COLLECTION_RECORD,
    COLLECTION_SCHEMA_VERSION,
    CollectionArtifacts,
    analyze_collected_workflow,
    collect_workflow_outputs,
)
from .execution import JobResult, JobSpec
from .manifest import (
    MAX_WORKFLOW_MANIFEST_BYTES,
    WorkflowManifestError,
    dump_starter_manifest,
    load_workflow_manifest,
    starter_workflow_manifest,
)
from .models import (
    WORKFLOW_SCHEMA_VERSION,
    ExecutableSpec,
    JSONScalar,
    JSONValue,
    SchedulerSpec,
    WorkflowManifest,
    WorkflowProject,
    WorkflowStep,
)
from .selection import (
    FrameSelectionError,
    parse_frame_selection,
    resolve_frame_selection,
    resolve_selected_frames,
    validate_frame_selection,
)
from .staging import (
    WORKSPACE_RECORD,
    WORKSPACE_SCHEMA_VERSION,
    StagedFrame,
    StagedWorkflow,
    descriptor_outputs,
    load_staged_workflow,
    stage_workflow,
)
from .validation import (
    WORKFLOW_VALIDATION_SCHEMA_VERSION,
    WorkflowValidation,
    validate_workflow,
)

__all__ = [
    "COLLECTION_DIRECTORY",
    "COLLECTION_RECORD",
    "COLLECTION_SCHEMA_VERSION",
    "MAX_WORKFLOW_MANIFEST_BYTES",
    "WORKFLOW_SCHEMA_VERSION",
    "WORKFLOW_VALIDATION_SCHEMA_VERSION",
    "WORKSPACE_RECORD",
    "WORKSPACE_SCHEMA_VERSION",
    "CollectionArtifacts",
    "ExecutableSpec",
    "FrameSelectionError",
    "JSONScalar",
    "JSONValue",
    "JobResult",
    "JobSpec",
    "SchedulerSpec",
    "StagedFrame",
    "StagedWorkflow",
    "WorkflowManifest",
    "WorkflowManifestError",
    "WorkflowProject",
    "WorkflowStep",
    "WorkflowValidation",
    "analyze_collected_workflow",
    "collect_workflow_outputs",
    "descriptor_outputs",
    "dump_starter_manifest",
    "load_staged_workflow",
    "load_workflow_manifest",
    "parse_frame_selection",
    "resolve_frame_selection",
    "resolve_selected_frames",
    "stage_workflow",
    "starter_workflow_manifest",
    "validate_frame_selection",
    "validate_workflow",
]
