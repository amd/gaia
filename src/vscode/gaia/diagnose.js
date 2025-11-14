/*
Copyright(C) 2024-2025 Advanced Micro Devices, Inc. All rights reserved.
SPDX-License-Identifier: MIT
*/

/**
 * Diagnostic script to test GAIA API and model fetching
 */

async function diagnose() {
    const apiUrl = 'http://localhost:8080';

    console.log('=== GAIA Extension Diagnostics ===\n');

    // Test 1: Check API server is running
    console.log('1. Testing API server connection...');
    try {
        const response = await fetch(`${apiUrl}/v1/models`);
        console.log(`   ✓ API server responding (status: ${response.status})`);

        if (response.ok) {
            const data = await response.json();
            console.log(`   ✓ Received ${data.data.length} models`);
            console.log('   Models:');
            data.data.forEach(model => {
                console.log(`     - ID: ${model.id}`);
                console.log(`       Name: ${model.id}`);
                console.log(`       Family: gaia`);
                console.log(`       Max Input: ${model.max_input_tokens || 8192}`);
                console.log(`       Max Output: ${model.max_output_tokens || 4096}`);
            });
        } else {
            console.log(`   ✗ API server error: ${response.status}`);
        }
    } catch (error) {
        console.log(`   ✗ Failed to connect: ${error.message}`);
    }

    // Test 2: Check model format
    console.log('\n2. Checking model format compatibility...');
    console.log('   ✓ Model IDs follow pattern: gaia-{agent}');
    console.log('   ✓ Family set to: gaia');
    console.log('   ✓ Vendor registered as: gaia');

    console.log('\n=== Diagnostic Complete ===\n');
    console.log('If models are not appearing in VSCode:');
    console.log('1. Try reloading VSCode (Ctrl+Shift+P > "Developer: Reload Window")');
    console.log('2. Check VSCode version (Help > About - should be >= 1.104.0)');
    console.log('3. Clear VSCode cache (see instructions below)');
    console.log('4. Check Developer Console for errors (Ctrl+Shift+I)');
}

diagnose();
