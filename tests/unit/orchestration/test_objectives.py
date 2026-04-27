"""
Unit tests for orchestration models.

Tests cover:
    - YAML round-trips (load/save)
    - Objective status transitions
    - DependencyGraph: circular detection, reverse index, cascade
    - Atomic write verification
    - Artifact serialization
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from gaia.orchestration.models import (
    Artifact,
    DependencyGraph,
    Objective,
    ObjectiveStatus,
    ProjectObjectives,
)


# =============================================================================
# Artifact tests
# =============================================================================


class TestArtifact:
    def test_default_artifact(self):
        artifact = Artifact(name="test", artifact_type="commit")
        assert artifact.name == "test"
        assert artifact.artifact_type == "commit"
        assert artifact.artifact_id  # auto-generated
        assert artifact.created_at  # auto-generated

    def test_artifact_to_dict(self):
        artifact = Artifact(
            name="v1.0",
            artifact_type="tag",
            url_or_path="https://github.com/repo/tag/v1.0",
        )
        d = artifact.to_dict()
        assert d["name"] == "v1.0"
        assert d["artifact_type"] == "tag"
        assert d["url_or_path"] == "https://github.com/repo/tag/v1.0"

    def test_artifact_from_dict(self):
        data = {
            "artifact_id": "abc123",
            "name": "build-artifact",
            "artifact_type": "binary",
            "url_or_path": "/dist/app.exe",
            "metadata": {"size": 1024},
        }
        artifact = Artifact.from_dict(data)
        assert artifact.artifact_id == "abc123"
        assert artifact.name == "build-artifact"
        assert artifact.artifact_type == "binary"
        assert artifact.metadata["size"] == 1024

    def test_artifact_round_trip(self):
        original = Artifact(
            name="round-trip",
            artifact_type="document",
            url_or_path="/docs/README.md",
            metadata={"author": "test"},
        )
        restored = Artifact.from_dict(original.to_dict())
        assert restored.name == original.name
        assert restored.artifact_type == original.artifact_type
        assert restored.url_or_path == original.url_or_path
        assert restored.metadata == original.metadata


# =============================================================================
# Objective status transition tests
# =============================================================================


class TestObjectiveStatusTransitions:
    def test_queued_to_in_progress(self):
        status = ObjectiveStatus.QUEUED
        assert status.can_transition_to(ObjectiveStatus.IN_PROGRESS)

    def test_in_progress_to_completed(self):
        status = ObjectiveStatus.IN_PROGRESS
        assert status.can_transition_to(ObjectiveStatus.COMPLETED)

    def test_queued_to_blocked(self):
        status = ObjectiveStatus.QUEUED
        assert status.can_transition_to(ObjectiveStatus.BLOCKED)

    def test_any_to_cancelled(self):
        # COMPLETED is terminal — cannot transition to anything
        for s in ObjectiveStatus:
            if s != ObjectiveStatus.CANCELLED and s != ObjectiveStatus.COMPLETED:
                assert s.can_transition_to(ObjectiveStatus.CANCELLED)

    def test_completed_is_terminal(self):
        status = ObjectiveStatus.COMPLETED
        for s in ObjectiveStatus:
            if s != ObjectiveStatus.COMPLETED:
                assert not status.can_transition_to(s)

    def test_cancelled_is_terminal(self):
        status = ObjectiveStatus.CANCELLED
        for s in ObjectiveStatus:
            if s != ObjectiveStatus.CANCELLED:
                assert not status.can_transition_to(s)

    def test_completed_to_in_progress_invalid(self):
        assert not ObjectiveStatus.COMPLETED.can_transition_to(
            ObjectiveStatus.IN_PROGRESS
        )


class TestObjectiveTransitions:
    def test_valid_transition_updates_status(self):
        obj = Objective(title="Test", status=ObjectiveStatus.QUEUED)
        obj.transition_to(ObjectiveStatus.IN_PROGRESS)
        assert obj.status == ObjectiveStatus.IN_PROGRESS

    def test_invalid_transition_raises(self):
        obj = Objective(title="Test", status=ObjectiveStatus.COMPLETED)
        with pytest.raises(ValueError, match="Invalid transition"):
            obj.transition_to(ObjectiveStatus.IN_PROGRESS)

    def test_transition_updates_timestamp(self):
        import time
        obj = Objective(title="Test")
        original_updated = obj.updated_at
        # Brief sleep to ensure timestamp granularity difference
        time.sleep(0.02)
        obj.transition_to(ObjectiveStatus.IN_PROGRESS)
        assert obj.updated_at != original_updated

    def test_add_artifact(self):
        obj = Objective(title="Test")
        artifact = Artifact(name="output", artifact_type="document")
        obj.add_artifact(artifact)
        assert len(obj.artifacts) == 1
        assert obj.artifacts[0].name == "output"


# =============================================================================
# Objective serialization
# =============================================================================


class TestObjectiveSerialization:
    def test_to_dict(self):
        obj = Objective(
            objective_id="obj-001",
            title="Build API",
            description="Create REST endpoints",
            status=ObjectiveStatus.QUEUED,
            dependencies=["obj-000"],
            phase="DEVELOPMENT",
        )
        d = obj.to_dict()
        assert d["objective_id"] == "obj-001"
        assert d["status"] == "queued"
        assert d["dependencies"] == ["obj-000"]

    def test_from_dict(self):
        data = {
            "objective_id": "obj-002",
            "title": "Design schema",
            "description": "Database schema design",
            "status": "completed",
            "dependencies": [],
            "artifacts": [],
            "priority": 2,
            "phase": "PLANNING",
            "pipeline_config": {},
        }
        obj = Objective.from_dict(data)
        assert obj.objective_id == "obj-002"
        assert obj.status == ObjectiveStatus.COMPLETED
        assert obj.priority == 2

    def test_round_trip(self):
        original = Objective(
            title="Round trip",
            description="Test serialization",
            priority=3,
            phase="QUALITY",
        )
        restored = Objective.from_dict(original.to_dict())
        assert restored.title == original.title
        assert restored.status == original.status
        assert restored.priority == original.priority


# =============================================================================
# ProjectObjectives tests
# =============================================================================


class TestProjectObjectives:
    @pytest.fixture
    def sample_project(self):
        return ProjectObjectives(
            project_id="proj-001",
            name="Sample Project",
            objectives=[
                Objective(
                    objective_id="obj-001",
                    title="Design",
                    description="System design",
                    status=ObjectiveStatus.COMPLETED,
                    priority=1,
                    phase="PLANNING",
                ),
                Objective(
                    objective_id="obj-002",
                    title="Implement",
                    description="Core implementation",
                    status=ObjectiveStatus.QUEUED,
                    dependencies=["obj-001"],
                    priority=2,
                    phase="DEVELOPMENT",
                ),
                Objective(
                    objective_id="obj-003",
                    title="Test",
                    description="Integration tests",
                    status=ObjectiveStatus.QUEUED,
                    dependencies=["obj-002"],
                    priority=3,
                    phase="QUALITY",
                ),
            ],
        )

    def test_get_objective(self, sample_project):
        found = sample_project.get_objective("obj-001")
        assert found is not None
        assert found.title == "Design"

    def test_get_objective_missing(self, sample_project):
        assert sample_project.get_objective("nonexistent") is None

    def test_get_ready_objectives(self, sample_project):
        ready = sample_project.get_ready_objectives()
        # Only obj-002 should be ready (dep obj-001 is completed)
        assert len(ready) == 1
        assert ready[0].objective_id == "obj-002"

    def test_get_ready_empty_when_all_deps_met(self):
        project = ProjectObjectives(
            objectives=[
                Objective(
                    objective_id="a",
                    title="A",
                    status=ObjectiveStatus.QUEUED,
                ),
            ],
        )
        ready = project.get_ready_objectives()
        assert len(ready) == 1
        assert ready[0].objective_id == "a"

    def test_add_objective(self):
        project = ProjectObjectives()
        obj = Objective(title="New task")
        project.add_objective(obj)
        assert len(project.objectives) == 1

    def test_get_all_objective_ids(self, sample_project):
        ids = sample_project.get_all_objective_ids()
        assert ids == {"obj-001", "obj-002", "obj-003"}

    # --- YAML serialization ---

    def test_yaml_round_trip(self, sample_project, tmp_path):
        yaml_str = sample_project.to_yaml()
        restored = ProjectObjectives.from_yaml_string(yaml_str)
        assert restored.project_id == sample_project.project_id
        assert len(restored.objectives) == 3
        assert restored.objectives[0].title == "Design"

    def test_load_nonexistent_file(self, tmp_path):
        path = str(tmp_path / "does-not-exist.yaml")
        project = ProjectObjectives.load(path)
        assert len(project.objectives) == 0

    def test_load_existing_file(self, tmp_path, sample_project):
        path = str(tmp_path / "objectives.yaml")
        sample_project.save_atomic(path)
        loaded = ProjectObjectives.load(path)
        assert loaded.project_id == sample_project.project_id
        assert len(loaded.objectives) == 3

    # --- Atomic write verification ---

    def test_atomic_write_no_corruption(self, tmp_path):
        """Verify that atomic writes do not leave partial/corrupt files."""
        path = str(tmp_path / "objectives.yaml")

        project = ProjectObjectives(
            project_id="atomic-test",
            name="Atomic Write Test",
        )
        for i in range(5):
            project.add_objective(Objective(title=f"Objective {i}"))

        project.save_atomic(path)

        # File should exist and be valid YAML
        assert os.path.exists(path)
        assert not os.path.exists(path + ".tmp")  # No leftover .tmp file

        with open(path, "r") as f:
            data = yaml.safe_load(f.read())
        assert data["project_id"] == "atomic-test"
        assert len(data["objectives"]) == 5

    def test_atomic_write_cleanup_on_failure(self, tmp_path):
        """Verify that temp files are cleaned up on write failure."""
        path = str(tmp_path / "objectives.yaml")
        tmp_file = path + ".tmp"

        project = ProjectObjectives(
            project_id="fail-test",
        )

        # Create a scenario where write fails after tmp creation
        with patch("builtins.open", side_effect=PermissionError("Denied")):
            with pytest.raises(PermissionError):
                project.save_atomic(path)

        # No leftover .tmp file
        assert not os.path.exists(tmp_file)

    def test_atomic_write_overwrite_existing(self, tmp_path):
        """Verify atomic overwrite replaces existing file cleanly."""
        path = str(tmp_path / "objectives.yaml")

        # Write v1
        v1 = ProjectObjectives(project_id="v1")
        v1.add_objective(Objective(title="v1 task"))
        v1.save_atomic(path)

        # Write v2
        v2 = ProjectObjectives(project_id="v2")
        v2.add_objective(Objective(title="v2 task"))
        v2.add_objective(Objective(title="v2 task 2"))
        v2.save_atomic(path)

        # Verify v2
        loaded = ProjectObjectives.load(path)
        assert loaded.project_id == "v2"
        assert len(loaded.objectives) == 2
        assert not os.path.exists(path + ".tmp")


# =============================================================================
# DependencyGraph tests
# =============================================================================


class TestDependencyGraph:
    def test_build_from_objectives(self):
        objectives = [
            Objective(objective_id="a", title="A", dependencies=[]),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["a", "b"]),
        ]
        graph = DependencyGraph(objectives)
        assert graph.nodes == {"a", "b", "c"}

    def test_get_dependencies(self):
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        assert graph.get_dependencies("b") == {"a"}
        assert graph.get_dependencies("a") == set()

    def test_reverse_dependencies(self):
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        reverse_a = graph.get_reverse_deps("a")
        assert reverse_a == {"b", "c"}

    def test_add_objective_to_graph(self):
        graph = DependencyGraph()
        obj = Objective(objective_id="x", title="X", dependencies=[])
        graph.add_objective(obj)
        assert "x" in graph.nodes

    def test_remove_objective_from_graph(self):
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        graph.remove_objective("a")
        assert "a" not in graph.nodes
        # b's forward reference to a should be removed
        assert "a" not in graph.get_dependencies("b")

    def test_detect_no_cycles(self):
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["b"]),
        ]
        graph = DependencyGraph(objectives)
        cycles = graph.detect_cycles()
        assert cycles == []

    def test_detect_cycles_simple(self):
        """A depends on B, B depends on A -> cycle."""
        objectives = [
            Objective(objective_id="a", title="A", dependencies=["b"]),
            Objective(objective_id="b", title="B", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        cycles = graph.detect_cycles()
        assert len(cycles) > 0

    def test_detect_cycles_triangle(self):
        """A -> B -> C -> A -> cycle."""
        objectives = [
            Objective(objective_id="a", title="A", dependencies=["c"]),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["b"]),
        ]
        graph = DependencyGraph(objectives)
        cycles = graph.detect_cycles()
        assert len(cycles) > 0

    def test_topological_order(self):
        objectives = [
            Objective(objective_id="c", title="C", dependencies=["b"]),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="a", title="A"),
        ]
        graph = DependencyGraph(objectives)
        order = graph.topological_order()
        assert order.index("a") < order.index("b") < order.index("c")

    def test_topological_order_raises_on_cycle(self):
        objectives = [
            Objective(objective_id="a", title="A", dependencies=["b"]),
            Objective(objective_id="b", title="B", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        with pytest.raises(ValueError, match="Circular dependencies"):
            graph.topological_order()

    def test_compute_cascade(self):
        """If A changes, cascade should include everything that depends on A."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["b"]),
            Objective(objective_id="d", title="D", dependencies=["a"]),
        ]
        graph = DependencyGraph(objectives)
        cascade = graph.compute_cascade("a")
        assert cascade == {"b", "c", "d"}

    def test_max_cascade_depth(self):
        """Linear chain: a -> b -> c -> d, depth from a should be 3."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["b"]),
            Objective(objective_id="d", title="D", dependencies=["c"]),
        ]
        graph = DependencyGraph(objectives)
        assert graph.max_cascade_depth("a") == 3
        assert graph.max_cascade_depth("d") == 0  # leaf node
        assert graph.max_cascade_depth("b") == 2

    def test_max_cascade_depth_diamond(self):
        """Diamond: a -> b, a -> c, b -> d, c -> d. depth from a should be 2."""
        objectives = [
            Objective(objective_id="a", title="A"),
            Objective(objective_id="b", title="B", dependencies=["a"]),
            Objective(objective_id="c", title="C", dependencies=["a"]),
            Objective(objective_id="d", title="D", dependencies=["b", "c"]),
        ]
        graph = DependencyGraph(objectives)
        # b depth = 1, c depth = 1, d depth = 2
        assert graph.max_cascade_depth("a") == 2

    def test_empty_graph(self):
        graph = DependencyGraph()
        assert graph.nodes == set()
        assert graph.detect_cycles() == []
        assert graph.topological_order() == []

    def test_external_dependency_ignored_in_cycles(self):
        """Dependencies on non-existent objectives should not cause false cycles."""
        objectives = [
            Objective(objective_id="a", title="A", dependencies=["external-id"]),
        ]
        graph = DependencyGraph(objectives)
        cycles = graph.detect_cycles()
        assert cycles == []
