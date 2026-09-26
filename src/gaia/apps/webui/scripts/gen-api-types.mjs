#!/usr/bin/env node
// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Generates src/types/api.gen.ts from the Agent UI backend's OpenAPI component
// schemas (src/types/api.schemas.json, written by `python -m gaia.ui.export_openapi`).
//
//   node scripts/gen-api-types.mjs          # rewrite api.gen.ts
//   node scripts/gen-api-types.mjs --check  # exit 1 if api.gen.ts is stale

import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { compile } from 'json-schema-to-typescript';

export const SCHEMAS_PATH = fileURLToPath(new URL('../src/types/api.schemas.json', import.meta.url));
export const OUTPUT_PATH = fileURLToPath(new URL('../src/types/api.gen.ts', import.meta.url));

export const REGENERATE_HINT =
    'python -m gaia.ui.export_openapi && (cd src/gaia/apps/webui && npm run gen:api-types)';

const BANNER = `// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// AUTO-GENERATED from the Agent UI backend's pydantic models — do not edit.
// Regenerate from the repo root: ${REGENERATE_HINT}
// Field names come straight from the wire; response fields with a backend
// default are optional here even though the server always sends them.`;

const REF_PREFIX = '#/components/schemas/';
const SUBSCHEMA_LIST_KEYS = ['anyOf', 'oneOf', 'allOf', 'prefixItems'];
const SUBSCHEMA_KEYS = ['items', 'additionalProperties', 'not'];

// Pydantic titles every property ("Agent Mode"); left in, each becomes its own
// exported type alias. Only schema-keyword `title`s are dropped — a *property*
// named `title` lives under `properties` and is kept.
function normalize(schema) {
    if (schema === null || typeof schema !== 'object') return schema;
    const out = {};
    for (const [key, value] of Object.entries(schema)) {
        if (key === 'title') continue;
        if (key === '$ref' && typeof value === 'string' && value.startsWith(REF_PREFIX)) {
            out.$ref = `#/definitions/${value.slice(REF_PREFIX.length)}`;
        } else if (key === 'properties') {
            out.properties = Object.fromEntries(
                Object.entries(value).map(([name, sub]) => [name, normalize(sub)]),
            );
        } else if (SUBSCHEMA_LIST_KEYS.includes(key)) {
            out[key] = value.map(normalize);
        } else if (SUBSCHEMA_KEYS.includes(key)) {
            out[key] = normalize(value);
        } else {
            out[key] = value;
        }
    }
    return out;
}

export async function generateApiTypes(schemas) {
    const names = Object.keys(schemas).sort();
    const definitions = Object.fromEntries(
        names.map((name) => [name, { ...normalize(schemas[name]), title: name }]),
    );
    // A root that references every schema so each one is emitted, plus an
    // `ApiSchemas` lookup keyed by the backend model name.
    const root = {
        title: 'ApiSchemas',
        type: 'object',
        properties: Object.fromEntries(names.map((name) => [name, { $ref: `#/definitions/${name}` }])),
        required: names,
        additionalProperties: false,
        definitions,
    };
    return compile(root, 'ApiSchemas', {
        additionalProperties: false,
        bannerComment: BANNER,
        format: true,
        strictIndexSignatures: true,
        unknownAny: true,
    });
}

async function main() {
    const schemas = JSON.parse(await readFile(SCHEMAS_PATH, 'utf-8'));
    const generated = await generateApiTypes(schemas);
    if (process.argv.includes('--check')) {
        const committed = await readFile(OUTPUT_PATH, 'utf-8');
        if (committed !== generated) {
            console.error(
                `${OUTPUT_PATH} is out of date with api.schemas.json.\n` +
                    `Regenerate from the repo root: ${REGENERATE_HINT}`,
            );
            process.exit(1);
        }
        return;
    }
    await writeFile(OUTPUT_PATH, generated);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
    await main();
}
