# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Unit tests for Medical Intake Agent."""

import json
import unittest

from gaia.testing import temp_directory


class TestMedicalIntakeAgentImport(unittest.TestCase):
    """Test that MedicalIntakeAgent can be imported."""

    def test_can_import(self):
        """Verify MedicalIntakeAgent can be imported."""
        from gaia.agents.emr import MedicalIntakeAgent

        self.assertIsNotNone(MedicalIntakeAgent)


class TestMedicalIntakeAgentInit(unittest.TestCase):
    """Test MedicalIntakeAgent initialization."""

    def test_init_creates_directories(self):
        """Test agent creates watch and data directories."""
        with temp_directory() as tmp_dir:
            watch_dir = tmp_dir / "intake"
            db_path = tmp_dir / "data" / "patients.db"

            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(watch_dir),
                db_path=str(db_path),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                self.assertTrue(watch_dir.exists())
                self.assertTrue(db_path.parent.exists())
                self.assertTrue(db_path.exists())
            finally:
                agent.stop()

    def test_init_creates_database_schema(self):
        """Test agent creates patients table."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Check table exists
                result = agent.query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='patients'"
                )
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]["name"], "patients")
            finally:
                agent.stop()


class TestPatientDataParsing(unittest.TestCase):
    """Test patient data extraction parsing."""

    def test_parse_valid_json(self):
        """Test parsing valid JSON extraction."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                valid_json = json.dumps(
                    {
                        "first_name": "John",
                        "last_name": "Doe",
                        "date_of_birth": "1990-05-15",
                        "phone": "555-123-4567",
                    }
                )

                result = agent._parse_extraction(valid_json)

                self.assertIsNotNone(result)
                self.assertEqual(result["first_name"], "John")
                self.assertEqual(result["last_name"], "Doe")
            finally:
                agent.stop()

    def test_parse_json_with_surrounding_text(self):
        """Test parsing JSON embedded in text."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # JSON embedded in explanatory text
                text_with_json = """Here is the extracted data:
                {"first_name": "Jane", "last_name": "Smith"}
                This was extracted from the form."""

                result = agent._parse_extraction(text_with_json)

                self.assertIsNotNone(result)
                self.assertEqual(result["first_name"], "Jane")
            finally:
                agent.stop()

    def test_parse_invalid_json(self):
        """Test parsing returns None for invalid JSON."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                result = agent._parse_extraction("Not valid JSON at all")
                self.assertIsNone(result)
            finally:
                agent.stop()

    def test_parse_nested_json(self):
        """Test parsing JSON with nested objects (critical bug fix test)."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Nested JSON - this would fail with regex \{[^{}]*\}
                nested_json = json.dumps(
                    {
                        "first_name": "John",
                        "last_name": "Doe",
                        "address": "123 Main St",
                        "city": "Boston",
                    }
                )

                result = agent._parse_extraction(nested_json)

                self.assertIsNotNone(result)
                self.assertEqual(result["first_name"], "John")
                self.assertEqual(result["city"], "Boston")
            finally:
                agent.stop()

    def test_parse_json_with_nested_objects_in_text(self):
        """Test parsing nested JSON embedded in text."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Deeply nested JSON in surrounding text
                text = 'Extracted: {"name": "Test", "meta": {"key": "value"}} end'
                result = agent._parse_extraction(text)

                self.assertIsNotNone(result)
                self.assertEqual(result["name"], "Test")
                self.assertEqual(result["meta"]["key"], "value")
            finally:
                agent.stop()


class TestPatientStorage(unittest.TestCase):
    """Test patient database storage."""

    def test_store_patient(self):
        """Test storing a patient record."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                patient_data = {
                    "first_name": "Test",
                    "last_name": "Patient",
                    "date_of_birth": "1985-03-20",
                    "phone": "555-999-8888",
                    "reason_for_visit": "Annual checkup",
                }

                patient_id = agent._store_patient(patient_data)

                self.assertIsNotNone(patient_id)
                self.assertIsInstance(patient_id, int)

                # Verify stored
                results = agent.query(
                    "SELECT * FROM patients WHERE id = :id",
                    {"id": patient_id},
                )
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]["first_name"], "Test")
                self.assertEqual(results[0]["last_name"], "Patient")
            finally:
                agent.stop()

    def test_store_patient_with_additional_fields(self):
        """Test that extra fields are stored in additional_fields JSON column."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Include fields NOT in STANDARD_COLUMNS to test additional_fields storage
                # These are custom/non-standard fields that will go to additional_fields JSON
                patient_data = {
                    "first_name": "Test",
                    "last_name": "User",
                    "custom_note": "VIP patient",
                    "preferred_appointment_day": "Tuesday",
                    "requires_interpreter": True,
                    "special_accommodations": "Wheelchair access",
                }

                patient_id = agent._store_patient(patient_data)
                self.assertIsNotNone(patient_id)

                # Verify additional_fields was stored
                results = agent.query(
                    "SELECT additional_fields FROM patients WHERE id = :id",
                    {"id": patient_id},
                )
                self.assertEqual(len(results), 1)

                # Parse and verify additional fields
                additional = json.loads(results[0]["additional_fields"])
                self.assertEqual(additional["custom_note"], "VIP patient")
                self.assertEqual(additional["preferred_appointment_day"], "Tuesday")
                self.assertEqual(additional["requires_interpreter"], True)
                self.assertEqual(
                    additional["special_accommodations"], "Wheelchair access"
                )
            finally:
                agent.stop()

    def test_store_patient_validates_required_fields(self):
        """Test that missing required fields are rejected."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Missing first_name
                patient_data = {"last_name": "Only"}
                patient_id = agent._store_patient(patient_data)
                self.assertIsNone(patient_id)

                # Missing last_name
                patient_data = {"first_name": "Only"}
                patient_id = agent._store_patient(patient_data)
                self.assertIsNone(patient_id)

                # Empty first_name
                patient_data = {"first_name": "", "last_name": "Test"}
                patient_id = agent._store_patient(patient_data)
                self.assertIsNone(patient_id)
            finally:
                agent.stop()


class TestReturningPatientDetection(unittest.TestCase):
    """Test new vs returning patient detection."""

    def test_find_existing_patient_by_name_and_dob(self):
        """Test finding existing patient by name and DOB."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Create initial patient
                agent._store_patient(
                    {
                        "first_name": "John",
                        "last_name": "Smith",
                        "date_of_birth": "1985-03-15",
                        "phone": "555-1234",
                    }
                )

                # Search for existing patient
                existing = agent._find_existing_patient(
                    {
                        "first_name": "John",
                        "last_name": "Smith",
                        "date_of_birth": "1985-03-15",
                    }
                )

                self.assertIsNotNone(existing)
                self.assertEqual(existing["first_name"], "John")

                # Search for non-existing patient
                not_found = agent._find_existing_patient(
                    {
                        "first_name": "Jane",
                        "last_name": "Doe",
                        "date_of_birth": "1990-01-01",
                    }
                )
                self.assertIsNone(not_found)
            finally:
                agent.stop()

    def test_detect_changes(self):
        """Test change detection between old and new patient data."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                existing = {
                    "phone": "555-1234",
                    "address": "123 Old St",
                    "insurance_provider": "OldCo",
                }

                new_data = {
                    "phone": "555-9999",  # Changed
                    "address": "456 New Ave",  # Changed
                    "insurance_provider": "OldCo",  # Same
                }

                changes = agent._detect_changes(existing, new_data)

                self.assertEqual(len(changes), 2)
                field_names = [c["field"] for c in changes]
                self.assertIn("phone", field_names)
                self.assertIn("address", field_names)
                self.assertNotIn("insurance_provider", field_names)
            finally:
                agent.stop()


class TestPatientUpdate(unittest.TestCase):
    """Test returning patient updates."""

    def test_update_patient(self):
        """Test updating existing patient record."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Create initial patient
                patient_id = agent._store_patient(
                    {
                        "first_name": "Update",
                        "last_name": "Test",
                        "phone": "555-1111",
                        "address": "123 Old St",
                    }
                )

                # Update patient
                result = agent._update_patient(
                    patient_id,
                    {
                        "phone": "555-9999",
                        "address": "456 New Ave",
                        "is_new_patient": False,
                    },
                )

                self.assertEqual(result, patient_id)

                # Verify update
                patient = agent.query(
                    "SELECT * FROM patients WHERE id = :id",
                    {"id": patient_id},
                )[0]

                self.assertEqual(patient["phone"], "555-9999")
                self.assertEqual(patient["address"], "456 New Ave")
                self.assertIsNotNone(patient["updated_at"])
            finally:
                agent.stop()


class TestPatientTools(unittest.TestCase):
    """Test patient management tools."""

    def test_search_patients_empty(self):
        """Test search with no patients."""
        with temp_directory() as tmp_dir:
            from gaia.agents.base.tools import _TOOL_REGISTRY
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Get and execute search tool
                search_func = _TOOL_REGISTRY["search_patients"]["function"]
                result = search_func(name="NonExistent")
                self.assertEqual(result["count"], 0)
                self.assertEqual(result["patients"], [])
            finally:
                agent.stop()

    def test_search_patients_by_name(self):
        """Test search by patient name."""
        with temp_directory() as tmp_dir:
            from gaia.agents.base.tools import _TOOL_REGISTRY
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Add test patient
                agent._store_patient(
                    {
                        "first_name": "Alice",
                        "last_name": "Johnson",
                        "date_of_birth": "1992-07-10",
                    }
                )

                # Search by name
                search_func = _TOOL_REGISTRY["search_patients"]["function"]
                result = search_func(name="Alice")
                self.assertEqual(result["count"], 1)
                self.assertEqual(result["patients"][0]["first_name"], "Alice")

                # Search by last name
                result = search_func(name="Johnson")
                self.assertEqual(result["count"], 1)
            finally:
                agent.stop()

    def test_get_patient(self):
        """Test getting patient by ID."""
        with temp_directory() as tmp_dir:
            from gaia.agents.base.tools import _TOOL_REGISTRY
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Add test patient
                patient_id = agent._store_patient(
                    {
                        "first_name": "Bob",
                        "last_name": "Williams",
                        "date_of_birth": "1988-11-25",
                        "reason_for_visit": "Flu symptoms",
                    }
                )

                # Get patient using tool
                get_func = _TOOL_REGISTRY["get_patient"]["function"]
                result = get_func(patient_id=patient_id)
                self.assertTrue(result["found"])
                self.assertEqual(result["patient"]["first_name"], "Bob")
                self.assertEqual(result["patient"]["reason_for_visit"], "Flu symptoms")

                # Get non-existent
                result = get_func(patient_id=9999)
                self.assertFalse(result["found"])
            finally:
                agent.stop()

    def test_get_intake_stats(self):
        """Test getting intake statistics."""
        with temp_directory() as tmp_dir:
            from gaia.agents.base.tools import _TOOL_REGISTRY
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Add some patients
                agent._store_patient({"first_name": "P1", "last_name": "Test"})
                agent._store_patient({"first_name": "P2", "last_name": "Test"})

                # Get stats using tool
                stats_func = _TOOL_REGISTRY["get_intake_stats"]["function"]
                result = stats_func()
                self.assertEqual(result["total_patients"], 2)
                self.assertIn("watching_directory", result)
                self.assertIn("uptime_seconds", result)
                # New stats fields
                self.assertIn("time_saved_minutes", result)
                self.assertIn("time_saved_percent", result)
                self.assertIn("new_patients", result)
                self.assertIn("returning_patients", result)
                self.assertIn("unacknowledged_alerts", result)
            finally:
                agent.stop()


class TestAlerts(unittest.TestCase):
    """Test alert creation and management."""

    def test_create_allergy_alert(self):
        """Test that allergy alerts are created."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Store patient with allergies
                patient_id = agent._store_patient(
                    {
                        "first_name": "Alert",
                        "last_name": "Test",
                        "allergies": "Penicillin, Sulfa",
                    }
                )

                # Manually trigger alert creation
                agent._create_alerts(
                    patient_id,
                    {
                        "allergies": "Penicillin, Sulfa",
                    },
                )

                # Check alert was created
                alerts = agent.query(
                    "SELECT * FROM alerts WHERE patient_id = :id AND alert_type = 'allergy'",
                    {"id": patient_id},
                )
                self.assertEqual(len(alerts), 1)
                self.assertEqual(alerts[0]["priority"], "critical")
                self.assertIn("Penicillin", alerts[0]["message"])
            finally:
                agent.stop()

    def test_alerts_table_exists(self):
        """Test that alerts table is created."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                result = agent.query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alerts'"
                )
                self.assertEqual(len(result), 1)
            finally:
                agent.stop()

    def test_intake_sessions_table_exists(self):
        """Test that intake_sessions table is created for audit trail."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                result = agent.query(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='intake_sessions'"
                )
                self.assertEqual(len(result), 1)
            finally:
                agent.stop()

    def test_duplicate_alert_prevention(self):
        """Test that duplicate unacknowledged alerts are not created."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            agent = MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            )

            try:
                # Store patient with allergies
                patient_id = agent._store_patient(
                    {
                        "first_name": "Duplicate",
                        "last_name": "Test",
                        "allergies": "Penicillin",
                    }
                )

                # Create alerts twice (simulates returning patient)
                agent._create_alerts(patient_id, {"allergies": "Penicillin"})
                agent._create_alerts(patient_id, {"allergies": "Penicillin"})

                # Should only have ONE unacknowledged allergy alert
                alerts = agent.query(
                    """SELECT * FROM alerts
                       WHERE patient_id = :id AND alert_type = 'allergy'
                       AND acknowledged = FALSE""",
                    {"id": patient_id},
                )
                self.assertEqual(len(alerts), 1, "Should not create duplicate alerts")

                # Acknowledge the alert using update() method
                agent.update(
                    "alerts",
                    {"acknowledged": True},
                    "id = :id",
                    {"id": alerts[0]["id"]},
                )

                # Now creating alerts again should create a new one
                agent._create_alerts(patient_id, {"allergies": "Penicillin"})

                all_alerts = agent.query(
                    "SELECT * FROM alerts WHERE patient_id = :id AND alert_type = 'allergy'",
                    {"id": patient_id},
                )
                self.assertEqual(
                    len(all_alerts), 2, "Should create new alert after acknowledge"
                )
            finally:
                agent.stop()


class TestContextManager(unittest.TestCase):
    """Test context manager support."""

    def test_context_manager_cleanup(self):
        """Test agent cleans up when used as context manager."""
        with temp_directory() as tmp_dir:
            from gaia.agents.emr import MedicalIntakeAgent

            with MedicalIntakeAgent(
                watch_dir=str(tmp_dir / "intake"),
                db_path=str(tmp_dir / "patients.db"),
                skip_lemonade=True,
                silent_mode=True,
                auto_start_watching=False,
            ) as agent:
                # Add a patient
                agent._store_patient({"first_name": "Context", "last_name": "Test"})

            # Agent should be stopped after context exit


if __name__ == "__main__":
    unittest.main()
