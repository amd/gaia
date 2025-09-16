#!/usr/bin/env python
# Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Jira Test Runner - Easy way to run Jira agent tests.

Usage:
    python run_jira_tests.py --unit     # Run unit tests only
    python run_jira_tests.py --live     # Run live tests against real Jira
    python run_jira_tests.py --all      # Run all tests
    python run_jira_tests.py --demo     # Run interactive demo queries
"""

import asyncio
import argparse
import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from gaia.apps.jira.app import JiraApp


async def run_unit_tests():
    """Run pytest unit tests."""
    print("🧪 Running Jira Unit Tests...")
    import subprocess
    
    # Run pytest on the jira test file
    result = subprocess.run([
        sys.executable, "-m", "pytest", 
        "tests/test_jira.py", 
        "-v", "--tb=short"
    ], cwd=project_root)
    
    if result.returncode == 0:
        print("✅ All unit tests passed!")
    else:
        print("❌ Some unit tests failed!")
    
    return result.returncode == 0


async def run_live_tests():
    """Run tests against real Jira instance."""
    print("🌐 Running Live Jira Tests...")
    
    # Check credentials
    required_env = ['ATLASSIAN_SITE_URL', 'ATLASSIAN_API_KEY', 'ATLASSIAN_USER_EMAIL']
    missing = [key for key in required_env if not os.getenv(key)]
    
    if missing:
        print(f"❌ Missing required environment variables: {', '.join(missing)}")
        print("\nPlease set these variables:")
        for key in missing:
            print(f"  export {key}=your_value")
        return False

    app = JiraApp(debug=False)  # Less verbose for batch testing
    await app.connect()

    test_cases = [
        {
            "name": "Basic Search",
            "query": "what are my issues",
            "expect_success": True
        },
        {
            "name": "Issue Type Filter", 
            "query": "find all ideas",
            "expect_success": True
        },
        {
            "name": "Time-based Query",
            "query": "show me issues created this week", 
            "expect_success": True
        },
        {
            "name": "Complex Multi-criteria",
            "query": "show me ideas assigned to me",
            "expect_success": True
        },
        {
            "name": "Issue Creation",
            "query": "create an idea called Live Test Issue",
            "expect_success": True
        }
    ]

    passed = 0
    failed = 0

    for test in test_cases:
        print(f"\n📝 {test['name']}: {test['query']}")
        try:
            result = await app.execute_command(test['query'])
            
            if result.success == test['expect_success']:
                print("   ✅ PASSED")
                passed += 1
            else:
                print(f"   ❌ FAILED - Expected success={test['expect_success']}, got {result.success}")
                if result.error:
                    print(f"      Error: {result.error}")
                failed += 1
                
        except Exception as e:
            print(f"   ❌ EXCEPTION: {e}")
            failed += 1

    await app.disconnect()
    
    print(f"\n📊 Live Test Results: {passed} passed, {failed} failed")
    return failed == 0


async def run_demo_queries():
    """Run interactive demo with common queries."""
    print("🎯 Jira Agent Demo - Common Queries")
    
    # Check credentials
    required_env = ['ATLASSIAN_SITE_URL', 'ATLASSIAN_API_KEY', 'ATLASSIAN_USER_EMAIL']
    if not all(os.getenv(key) for key in required_env):
        print("❌ Jira credentials not found. Please set ATLASSIAN_* environment variables.")
        return False

    app = JiraApp(debug=True)  # Verbose for demo
    await app.connect()

    demo_queries = [
        "what are my issues",
        "find all ideas", 
        "show me issues created this week",
        "show me ideas assigned to me",
        "create an idea called Demo Test Issue"
    ]

    print("\n🚀 Running demo queries...\n")

    for i, query in enumerate(demo_queries, 1):
        print(f"{'='*60}")
        print(f"Demo Query {i}/{len(demo_queries)}: {query}")
        print('='*60)
        
        try:
            result = await app.execute_command(query)
            app._display_result(result)
        except Exception as e:
            print(f"❌ Error: {e}")
        
        if i < len(demo_queries):
            input("\nPress Enter for next query...")

    await app.disconnect()
    print("\n🎉 Demo completed!")
    return True


async def main():
    parser = argparse.ArgumentParser(description="Jira Test Runner")
    parser.add_argument("--unit", action="store_true", help="Run unit tests")
    parser.add_argument("--live", action="store_true", help="Run live tests")
    parser.add_argument("--demo", action="store_true", help="Run demo queries")
    parser.add_argument("--all", action="store_true", help="Run all tests")
    
    args = parser.parse_args()

    if not any([args.unit, args.live, args.demo, args.all]):
        parser.print_help()
        return 1

    results = []

    if args.unit or args.all:
        success = await run_unit_tests()
        results.append(("Unit Tests", success))

    if args.live or args.all:
        success = await run_live_tests()
        results.append(("Live Tests", success))

    if args.demo:
        success = await run_demo_queries()
        results.append(("Demo", success))

    # Summary
    if results:
        print(f"\n{'='*60}")
        print("📋 SUMMARY")
        print('='*60)
        
        all_passed = True
        for test_name, passed in results:
            status = "✅ PASSED" if passed else "❌ FAILED"
            print(f"{test_name:15} {status}")
            if not passed:
                all_passed = False
        
        if all_passed:
            print("\n🎉 All tests passed!")
            return 0
        else:
            print("\n⚠️  Some tests failed!")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))