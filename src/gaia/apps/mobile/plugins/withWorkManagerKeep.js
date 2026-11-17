// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const fs = require('node:fs');
const path = require('node:path');
const { withDangerousMod } = require('expo/config-plugins');

const BEGIN = '# @generated begin withWorkManagerKeep (plugins/withWorkManagerKeep.js)';
const END = '# @generated end withWorkManagerKeep';

// WorkManager (via expo-background-task) builds its Room database by reflection; R8 strips the constructor in release.
const RULES = `${BEGIN}
-keep class androidx.work.impl.WorkDatabase_Impl { <init>(...); }
-keep class * extends androidx.room.RoomDatabase { <init>(...); }
${END}`;

function withRules(contents) {
    const start = contents.indexOf(BEGIN);
    let rest = contents;
    if (start !== -1) {
        const end = contents.indexOf(END, start);
        if (end === -1) {
            throw new Error(`withWorkManagerKeep: proguard-rules.pro has "${BEGIN}" but no "${END}"; run \`npx expo prebuild --clean\`.`);
        }
        rest = contents.slice(0, start) + contents.slice(end + END.length);
    }
    const base = rest.trimEnd();
    return `${base}${base ? '\n\n' : ''}${RULES}\n`;
}

/** Keeps WorkManager's Room database constructor in release builds, which otherwise crash at launch. */
function withWorkManagerKeep(config) {
    return withDangerousMod(config, [
        'android',
        (cfg) => {
            const file = path.join(cfg.modRequest.platformProjectRoot, 'app', 'proguard-rules.pro');
            if (!fs.existsSync(file)) {
                throw new Error(`withWorkManagerKeep: ${file} is missing; the Android template changed. Update plugins/withWorkManagerKeep.js.`);
            }
            fs.writeFileSync(file, withRules(fs.readFileSync(file, 'utf8')));
            return cfg;
        },
    ]);
}

module.exports = withWorkManagerKeep;
module.exports.withRules = withRules;
