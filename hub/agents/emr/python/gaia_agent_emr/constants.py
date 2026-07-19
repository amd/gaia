# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""
Constants and schemas for the Medical Intake Agent.

This module contains database schemas, VLM prompts, and manual entry
time estimation logic used by the Medical Intake Agent.
"""

# =============================================================================
# MANUAL DATA ENTRY TIME ESTIMATION
# =============================================================================
# Based on research: average data entry speed is 40 WPM (~200 CPM) but medical
# forms require looking back and forth, finding fields in UI, and verification.
#
# Formula per field: (BASE_TIME + chars * TYPING_TIME) * COMPLEXITY * (1 + VERIFICATION)
#
# Example calculation for a typical form with 15 fields, ~300 total characters:
#   Base time: 15 fields * 10 sec = 150 sec
#   Typing: 300 chars * 0.3 sec = 90 sec
#   Subtotal: ~240 sec (4 min)
#   With verification (+15%): ~276 sec (4.6 min)
#
# This aligns with studies showing manual EMR data entry takes 3-8 minutes
# per patient depending on form complexity.

TYPING_SECONDS_PER_CHAR = 0.3  # Slower than typical typing due to form lookup
BASE_SECONDS_PER_FIELD = 10  # Time to locate field, click, prepare to type
VERIFICATION_OVERHEAD = 0.15  # 15% extra time for checking/verification

# Field complexity multipliers (some fields take longer to enter)
FIELD_COMPLEXITY = {
    # Simple fields - quick to enter
    "first_name": 1.0,
    "last_name": 1.0,
    "gender": 0.5,  # Usually a dropdown
    "phone": 1.0,
    "email": 1.0,
    "state": 0.5,  # Often a dropdown
    "zip_code": 0.8,
    # Medium complexity - require more attention
    "date_of_birth": 1.2,  # Date formatting
    "address": 1.2,
    "city": 1.0,
    "insurance_provider": 1.2,
    "insurance_id": 1.0,
    "emergency_contact_name": 1.0,
    "emergency_contact_phone": 1.0,
    # Complex fields - may have multiple items, require careful reading
    "reason_for_visit": 1.5,
    "allergies": 1.8,  # Critical field, needs careful entry
    "medications": 1.8,  # May have multiple items
}


def estimate_manual_entry_time(extracted_data: dict) -> float:
    """
    Estimate how long manual data entry would take for extracted form data.

    The estimation is based on:
    1. Number of fields extracted (base time per field)
    2. Character count of each field (typing time)
    3. Field complexity (some fields require more care)
    4. Verification overhead (checking entries)

    Args:
        extracted_data: Dictionary of extracted patient data

    Returns:
        Estimated manual entry time in seconds
    """
    total_seconds = 0.0
    field_count = 0

    # Fields to skip in estimation (metadata, not user-entered)
    skip_fields = {
        "source_file",
        "raw_extraction",
        "additional_fields",
        "is_new_patient",
        "processing_time_seconds",
        "file_hash",
        "file_content",
        "estimated_manual_seconds",
        "id",
        "created_at",
        "updated_at",
    }

    for field_name, value in extracted_data.items():
        if value is None or value == "" or field_name in skip_fields:
            continue

        # Convert value to string for character counting
        value_str = str(value)
        char_count = len(value_str)

        if char_count == 0:
            continue

        field_count += 1

        # Get complexity multiplier (default 1.0 for unknown fields)
        complexity = FIELD_COMPLEXITY.get(field_name, 1.0)

        # Calculate time for this field
        field_time = (
            BASE_SECONDS_PER_FIELD  # Base time to find and click field
            + (char_count * TYPING_SECONDS_PER_CHAR)  # Typing time
        ) * complexity

        total_seconds += field_time

    # Add verification overhead (checking all entries)
    total_seconds *= 1 + VERIFICATION_OVERHEAD

    # Minimum time if we have any fields (at least 30 seconds)
    if field_count > 0:
        total_seconds = max(total_seconds, 30.0)

    return round(total_seconds, 1)


# =============================================================================
# DATABASE SCHEMA
# =============================================================================

PATIENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Patient info - basic
    first_name TEXT,
    last_name TEXT,
    date_of_birth TEXT,
    age TEXT,
    gender TEXT,
    preferred_pronouns TEXT,
    ssn TEXT,
    marital_status TEXT,
    spouse_name TEXT,
    -- Contact info
    phone TEXT,
    mobile_phone TEXT,
    work_phone TEXT,
    email TEXT,
    address TEXT,
    city TEXT,
    state TEXT,
    zip_code TEXT,
    -- Demographics
    preferred_language TEXT,
    race TEXT,
    ethnicity TEXT,
    contact_preference TEXT,
    -- Emergency contact
    emergency_contact_name TEXT,
    emergency_contact_relationship TEXT,
    emergency_contact_phone TEXT,
    -- Physicians
    referring_physician TEXT,
    referring_physician_phone TEXT,
    primary_care_physician TEXT,
    preferred_pharmacy TEXT,
    -- Employment
    employment_status TEXT,
    occupation TEXT,
    employer TEXT,
    employer_address TEXT,
    -- Primary insurance
    insurance_provider TEXT,
    insurance_id TEXT,
    insurance_group_number TEXT,
    insured_name TEXT,
    insured_dob TEXT,
    insurance_phone TEXT,
    billing_address TEXT,
    guarantor_name TEXT,
    -- Secondary insurance
    secondary_insurance_provider TEXT,
    secondary_insurance_id TEXT,
    -- Medical/Pain history
    reason_for_visit TEXT,
    date_of_injury TEXT,
    pain_location TEXT,
    pain_onset TEXT,
    pain_cause TEXT,
    pain_progression TEXT,
    work_related_injury TEXT,
    car_accident TEXT,
    medical_conditions TEXT,
    allergies TEXT,
    medications TEXT,
    -- Form metadata
    form_date TEXT,
    signature_date TEXT,
    -- System fields
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    source_file TEXT,
    raw_extraction TEXT,
    additional_fields TEXT,
    is_new_patient BOOLEAN DEFAULT TRUE,
    processing_time_seconds REAL,
    estimated_manual_seconds REAL,
    file_hash TEXT,
    file_content BLOB
);

CREATE INDEX IF NOT EXISTS idx_patients_name ON patients(last_name, first_name);
CREATE INDEX IF NOT EXISTS idx_patients_dob ON patients(date_of_birth);
CREATE INDEX IF NOT EXISTS idx_patients_hash ON patients(file_hash);

-- Alerts table for tracking critical notifications
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id),
    alert_type TEXT NOT NULL,
    priority TEXT DEFAULT 'medium',
    message TEXT NOT NULL,
    data TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    acknowledged BOOLEAN DEFAULT FALSE,
    acknowledged_by TEXT,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_alerts_patient ON alerts(patient_id);
CREATE INDEX IF NOT EXISTS idx_alerts_acknowledged ON alerts(acknowledged);

-- Intake sessions for audit trail
CREATE TABLE IF NOT EXISTS intake_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER REFERENCES patients(id),
    source_file TEXT,
    processing_time_seconds REAL,
    is_new_patient BOOLEAN,
    changes_detected TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


# =============================================================================
# VLM EXTRACTION PROMPT
# =============================================================================

EXTRACTION_PROMPT = """You are a medical data extraction system. Extract ALL patient information from this intake form image.

Return a JSON object with ALL fields you can extract. Use these standard field names when applicable:

REQUIRED (always include):
- "first_name": patient's first name
- "last_name": patient's last name

PATIENT INFO:
- "form_date": date form was filled (YYYY-MM-DD)
- "date_of_birth": YYYY-MM-DD format
- "age": patient's age if listed
- "gender": Male/Female/Other
- "preferred_pronouns": he/him, she/her, they/them if listed
- "ssn": Social Security Number (XXX-XX-XXXX)
- "marital_status": Single/Married/Divorced/Widowed/Partnered
- "spouse_name": spouse's name if listed
- "phone": home phone number
- "mobile_phone": cell/mobile phone number
- "work_phone": work phone number
- "email"
- "address": street address
- "city", "state", "zip_code"
- "preferred_language": English/Spanish/etc if listed
- "race", "ethnicity": if listed
- "contact_preference": preferred contact method if listed

EMERGENCY CONTACT:
- "emergency_contact_name": name of emergency contact person
- "emergency_contact_relationship": relationship to patient (e.g. Mom, Spouse, Friend)
- "emergency_contact_phone": emergency contact's phone number

PHYSICIANS:
- "referring_physician": name of referring physician/doctor
- "referring_physician_phone": phone number next to referring physician
- "primary_care_physician": PCP name if different from referring
- "preferred_pharmacy": pharmacy name if listed

EMPLOYMENT:
- "employment_status": Employed/Self Employed/Unemployed/Retired/Student/Disabled/Military
- "occupation": job title if listed
- "employer": employer/company name
- "employer_address": employer address if listed

PRIMARY INSURANCE:
- "insurance_provider": insurance company name
- "insurance_id": policy number
- "insurance_group_number": group number
- "insured_name": name of insured person (may differ from patient)
- "insured_dob": DOB of insured (YYYY-MM-DD)
- "insurance_phone": insurance contact number
- "billing_address": billing address if different from home
- "guarantor_name": person responsible for payment if listed

SECONDARY INSURANCE:
- "secondary_insurance_provider", "secondary_insurance_id"

MEDICAL/PAIN HISTORY:
- "reason_for_visit": chief complaint or reason for visit
- "date_of_injury": date of injury or onset of symptoms (YYYY-MM-DD)
- "pain_location": where pain is located if listed
- "pain_onset": when pain began (e.g. three months ago)
- "pain_cause": what caused the pain/condition
- "pain_progression": Improved/Worsened/Stayed the same
- "work_related_injury": Yes/No
- "car_accident": Yes/No
- "medical_conditions": existing medical conditions
- "allergies": known allergies
- "medications": current medications

SIGNATURE:
- "signature_date": date signed (YYYY-MM-DD)

ADDITIONAL FIELDS:
Extract ANY other fields visible on the form using descriptive snake_case names.

IMPORTANT:
- Extract EVERY field visible on the form
- Use null for fields that exist but are blank
- Omit fields not present on this specific form
- Return ONLY the JSON object, no other text
- Dates must be in YYYY-MM-DD format
- For checkboxes, use Yes/No values"""


# Standard columns that map to database schema
STANDARD_COLUMNS = [
    # Patient info - basic
    "first_name",
    "last_name",
    "date_of_birth",
    "age",
    "gender",
    "preferred_pronouns",
    "ssn",
    "marital_status",
    "spouse_name",
    # Contact info
    "phone",  # home phone
    "mobile_phone",  # cell phone
    "work_phone",
    "email",
    "address",
    "city",
    "state",
    "zip_code",
    # Demographics
    "preferred_language",
    "race",
    "ethnicity",
    "contact_preference",
    # Emergency contact
    "emergency_contact_name",
    "emergency_contact_relationship",
    "emergency_contact_phone",
    # Physicians
    "referring_physician",
    "referring_physician_phone",
    "primary_care_physician",
    "preferred_pharmacy",
    # Employment
    "employment_status",
    "occupation",
    "employer",
    "employer_address",
    # Primary insurance
    "insurance_provider",
    "insurance_id",
    "insurance_group_number",
    "insured_name",
    "insured_dob",
    "insurance_phone",
    "billing_address",
    "guarantor_name",
    # Secondary insurance
    "secondary_insurance_provider",
    "secondary_insurance_id",
    # Medical/Pain history
    "reason_for_visit",
    "date_of_injury",
    "pain_location",
    "pain_onset",
    "pain_cause",
    "pain_progression",
    "work_related_injury",
    "car_accident",
    "medical_conditions",
    "allergies",
    "medications",
    # Form metadata
    "form_date",
    "signature_date",
    # System fields
    "source_file",
    "raw_extraction",
    "additional_fields",
    "is_new_patient",
    "processing_time_seconds",
    "estimated_manual_seconds",
    "file_hash",
    "file_content",
]

# Columns that can be updated for returning patients
UPDATABLE_COLUMNS = [
    # Contact info (can change)
    "phone",  # home phone
    "mobile_phone",  # cell phone
    "work_phone",
    "email",
    "address",
    "city",
    "state",
    "zip_code",
    "contact_preference",
    # Marital status (can change)
    "marital_status",
    "spouse_name",
    # Emergency contact (can change)
    "emergency_contact_name",
    "emergency_contact_relationship",
    "emergency_contact_phone",
    # Physicians (can change)
    "referring_physician",
    "referring_physician_phone",
    "primary_care_physician",
    "preferred_pharmacy",
    # Employment (can change)
    "employment_status",
    "occupation",
    "employer",
    "employer_address",
    # Insurance (can change)
    "insurance_provider",
    "insurance_id",
    "insurance_group_number",
    "insured_name",
    "insured_dob",
    "insurance_phone",
    "billing_address",
    "guarantor_name",
    "secondary_insurance_provider",
    "secondary_insurance_id",
    # Medical/Pain (can change each visit)
    "reason_for_visit",
    "date_of_injury",
    "pain_location",
    "pain_onset",
    "pain_cause",
    "pain_progression",
    "work_related_injury",
    "car_accident",
    "medical_conditions",
    "allergies",
    "medications",
    # Form metadata
    "form_date",
    "signature_date",
    # System fields
    "source_file",
    "raw_extraction",
    "additional_fields",
    "is_new_patient",
    "processing_time_seconds",
    "estimated_manual_seconds",
    "file_hash",
    "file_content",
]
