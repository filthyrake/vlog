#!/usr/bin/env python3
"""
Tests to verify .env.example is complete and matches config.py
"""

import re
from pathlib import Path


def extract_env_vars_from_config():
    """Extract all VLOG_ environment variable names from config.py"""
    config_path = Path(__file__).parent.parent / "config.py"
    content = config_path.read_text()

    # Find all os.getenv("VLOG_...") and os.environ.get("VLOG_...") calls
    # Explicitly match both quote types
    pattern = r'os\.(?:getenv|environ\.get)\(["\']VLOG_([A-Z_]+)["\']'
    matches = re.findall(pattern, content)

    return set(f"VLOG_{var}" for var in matches)


def extract_env_vars_from_cli():
    """Extract all VLOG_ environment variable names from cli/main.py"""
    cli_path = Path(__file__).parent.parent / "cli" / "main.py"
    content = cli_path.read_text()

    # Find all os.getenv("VLOG_...") calls
    pattern = r'os\.getenv\(["\']VLOG_([A-Z_]+)["\']'
    matches = re.findall(pattern, content)

    return set(f"VLOG_{var}" for var in matches)


def extract_env_vars_from_example():
    """Extract all VLOG_ environment variable names from .env.example"""
    example_path = Path(__file__).parent.parent / ".env.example"
    content = example_path.read_text()

    # Find all VLOG_ variable declarations (both commented and uncommented)
    # Pattern matches actual variable assignments, not comments containing examples
    pattern = r"^\s*#?\s*VLOG_([A-Z_]+)\s*="
    matches = re.findall(pattern, content, re.MULTILINE)

    return set(f"VLOG_{var}" for var in matches)


def test_env_example_completeness():
    """Test that .env.example contains all environment variables used in the code"""
    config_vars = extract_env_vars_from_config()
    cli_vars = extract_env_vars_from_cli()
    example_vars = extract_env_vars_from_example()

    # Combine all expected variables
    all_expected_vars = config_vars | cli_vars

    # Check for missing variables
    missing_vars = all_expected_vars - example_vars

    assert not missing_vars, (
        f"Missing environment variables in .env.example: {sorted(missing_vars)}. "
        f"Please add these variables to .env.example with appropriate defaults and comments."
    )


def test_env_example_no_undefined_vars():
    """Test that .env.example doesn't contain variables not used in the code"""
    config_vars = extract_env_vars_from_config()
    cli_vars = extract_env_vars_from_cli()
    example_vars = extract_env_vars_from_example()

    # Combine all expected variables
    all_expected_vars = config_vars | cli_vars

    # Check for extra variables (might be deprecated or test-only)
    extra_vars = example_vars - all_expected_vars

    # No exemptions needed - all variables should be used in code
    allowed_extra_vars = set()
    unexpected_extra_vars = extra_vars - allowed_extra_vars

    assert not unexpected_extra_vars, (
        f"Unexpected environment variables in .env.example: {sorted(unexpected_extra_vars)}. "
        f"These variables are not used in the code. Consider removing them or updating the code."
    )


def test_env_example_structure():
    """Test that .env.example has proper structure with comments and sections"""
    example_path = Path(__file__).parent.parent / ".env.example"
    content = example_path.read_text()

    # Check for section headers
    assert "# Storage Paths" in content, ".env.example should have a Storage Paths section"
    assert "# Server Ports" in content, ".env.example should have a Server Ports section"
    assert "# Worker Settings" in content, ".env.example should have a Worker Settings section"
    assert "# HLS Settings" in content, ".env.example should have a HLS Settings section"
    assert "# Transcoding Settings" in content, ".env.example should have a Transcoding Settings section"
    assert "# Transcription Settings" in content, ".env.example should have a Transcription Settings section"
    assert "# Upload Limits" in content, ".env.example should have an Upload Limits section"
    assert "# CORS Configuration" in content, ".env.example should have a CORS Configuration section"
    assert "# Rate Limiting" in content, ".env.example should have a Rate Limiting section"
    assert "# Analytics Caching" in content, ".env.example should have an Analytics Caching section"
    assert "# CLI Configuration" in content, ".env.example should have a CLI Configuration section"
    assert "# Testing" in content, ".env.example should have a Testing section"

    # Check for header comment
    assert "VLog Configuration" in content, ".env.example should have a VLog Configuration header"
    assert "Copy this file to .env" in content, ".env.example should have usage instructions"
