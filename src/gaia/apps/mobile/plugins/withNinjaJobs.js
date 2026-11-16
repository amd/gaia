// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const { withProjectBuildGradle } = require('expo/config-plugins');

const BEGIN = '// @generated begin withNinjaJobs (plugins/withNinjaJobs.js)';
const END = '// @generated end withNinjaJobs';

// Ninja otherwise runs a compile per core in every C++ module Gradle builds at once.
const BLOCK = `${BEGIN}
def heavyJobs = providers.environmentVariable('GAIA_BUILD_JOBS').getOrElse('4')
if (!(heavyJobs ==~ /[1-9][0-9]*/)) {
    throw new GradleException("GAIA_BUILD_JOBS must be a whole number of 1 or more, not '\${heavyJobs}'. Unset it to compile 4 files at a time.")
}
subprojects { sub ->
    ['com.android.application', 'com.android.library'].each { pluginId ->
        sub.pluginManager.withPlugin(pluginId) {
            sub.androidComponents.finalizeDsl({ dsl ->
                // Plain Strings: a GString in this List<String> fails when AGP reads it.
                dsl.defaultConfig.externalNativeBuild.cmake.arguments.addAll([
                    '-DCMAKE_JOB_POOLS=heavy_jobs=' + heavyJobs,
                    '-DCMAKE_JOB_POOL_COMPILE=heavy_jobs',
                    '-DCMAKE_JOB_POOL_LINK=heavy_jobs',
                ])
            } as Action)
        }
    }
}
${END}`;

function withoutBlock(contents) {
    const start = contents.indexOf(BEGIN);
    if (start === -1) return contents;
    const end = contents.indexOf(END, start);
    if (end === -1) {
        throw new Error(`withNinjaJobs: android/build.gradle has "${BEGIN}" but no "${END}"; run \`npx expo prebuild --clean\`.`);
    }
    return contents.slice(0, start) + contents.slice(end + END.length);
}

/** Caps the parallel C++ compiles of an Android build at GAIA_BUILD_JOBS (default 4), read when Gradle runs. */
module.exports = function withNinjaJobs(config) {
    return withProjectBuildGradle(config, (cfg) => {
        if (cfg.modResults.language !== 'groovy') {
            throw new Error(
                `withNinjaJobs: android/build.gradle is ${cfg.modResults.language}, not Groovy; port the block in plugins/withNinjaJobs.js to it.`,
            );
        }
        cfg.modResults.contents = `${withoutBlock(cfg.modResults.contents).trimEnd()}\n\n${BLOCK}\n`;
        return cfg;
    });
};
