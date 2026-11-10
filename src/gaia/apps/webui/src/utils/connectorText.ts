// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

import { isMessageKey } from '../i18n/registry';
import { tEn, translator, type Translate } from '../i18n/useT';

interface Field {
    key: string;
    label: string;
    help_md: string;
}

/**
 * Catalog connectors are keyed under `connectors.<id>`. Text with no key (a custom
 * server) or whose English no longer matches ours (a reworded catalog) shows as sent.
 */
function catalogText(t: Translate, key: string, server: string, bare = false): string {
    if (!isMessageKey(key) || tEn(key) !== server) return server;
    return bare ? t.bare(key) : t(key);
}

export function connectorDescription(t: Translate, connector: { id: string; description: string }): string {
    return catalogText(t, `connectors.${connector.id}.description`, connector.description);
}

/** The description in the chosen language only, for matching a search. */
export function connectorDescriptionBare(t: Translate, connector: { id: string; description: string }): string {
    return catalogText(t, `connectors.${connector.id}.description`, connector.description, true);
}

export function fieldLabel(t: Translate, connectorId: string, field: Field, bare = false): string {
    return catalogText(t, `connectors.${connectorId}.${field.key}.label`, field.label, bare);
}

export function fieldHelp(t: Translate, connectorId: string, field: Field): string {
    return catalogText(t, `connectors.${connectorId}.${field.key}.help`, field.help_md);
}

/** "Required: <labels>", each half listing the labels in its own language. */
export function missingFieldsMessage(t: Translate, connectorId: string, fields: Field[]): string {
    return t.compose((say, code) => {
        const own = translator(code);
        const labels = fields.map((f) =>
            catalogText(own, `connectors.${connectorId}.${f.key}.label`, f.label, true),
        );
        return say('settings.connectors.required', { fields: labels.join(say('settings.connectors.fieldsJoin')) });
    });
}
