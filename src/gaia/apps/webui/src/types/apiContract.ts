// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

/**
 * Compile-time drift guard between the hand-written wire types in ./index.ts
 * and the types generated from the backend's pydantic models (./api.gen.ts).
 *
 * `tsc` fails here when the backend sends a field the hand-written type lacks,
 * the hand-written type declares a field the backend never sends, or a shared
 * field's kind set (string / number / boolean / null / array / object) differs
 * in either direction — a TS type too narrow for the wire, or one that allows a
 * kind (typically `null`) the backend never sends.
 * The error names the check (position 0 = missing from the TS type, 1 = not on
 * the wire, 2 = kind mismatch) and the field, e.g. `Type '"agent_mode"' is not
 * assignable to type 'never'`. Fix the hand-written type, or regenerate
 * api.gen.ts if the backend changed (see its header).
 *
 * Camel-cased view models the UI maps from the wire (Message, AgentStep,
 * CommandOutput) are not wire types and are deliberately not listed.
 */

import type * as Api from './api.gen';
import type {
    AgentInfo,
    BrowseResponse,
    DiskAgentInfo,
    Document,
    DownloadProgress,
    FileEntry,
    IndexFolderResponse,
    InferenceStats,
    ModelStatus,
    OnboardingStatusResponse,
    ParsedSchedule,
    PreflightReport,
    QuickLink,
    Schedule,
    Session,
    Settings,
    SourceInfo,
    SystemStatus,
} from './index';

type Kind<T> = T extends null
    ? 'null'
    : T extends string
      ? 'string'
      : T extends number
        ? 'number'
        : T extends boolean
          ? 'boolean'
          : T extends readonly unknown[]
            ? 'array'
            : T extends object
              ? 'object'
              : 'unknown';

type Shared<Wire, Ts> = keyof Wire & keyof Ts;

type MissingFromTs<Wire, Ts> = Exclude<keyof Wire, keyof Ts>;

type NotOnWire<Wire, Ts, TsOnly> = Exclude<keyof Ts, keyof Wire | TsOnly>;

// Backend defaults surface as `?:` in api.gen.ts but are always sent, so
// `undefined` is stripped from the wire side before comparing kinds.
//
// Equals, not `extends`: assignability only catches a TS type too NARROW for the
// wire. A TS type whose kinds are a superset — `string | null` for a field the
// backend always sends as `string` — passed silently and left dead null handling
// in the UI.
type KindMismatch<Wire, Ts> = {
    [K in Shared<Wire, Ts>]-?: Equals<Kind<Exclude<Wire[K], undefined>>, Kind<Exclude<Ts[K], undefined>>> extends true
        ? never
        : K;
}[Shared<Wire, Ts>];

type Drift<Wire, Ts, TsOnly extends PropertyKey = never> = [
    missingFromTs: MissingFromTs<Wire, Ts>,
    notOnWire: NotOnWire<Wire, Ts, TsOnly>,
    kindMismatch: KindMismatch<Wire, Ts>,
];

type NoDrift<T extends [never, never, never]> = T;

type Equals<A, B> = (<T>() => T extends A ? 1 : 2) extends <T>() => T extends B ? 1 : 2 ? true : false;
type Assert<T extends true> = T;

// Keeps the guard honest: if a refactor made Drift always `never`, every
// NoDrift below would pass vacuously.
export type DriftSelfTest = Assert<
    Equals<Drift<{ sent: string; kind: number | null }, { kind: number; extra: string }>, ['sent', 'extra', 'kind']>
>;

// The kind check must fire in BOTH directions. The direction below — a TS type
// permitting `null` for a field the backend always sends — is the one an
// assignability test silently passes; `lemonade_url` and `default_model_name`
// were both in that state.
export type KindMismatchIsSymmetricSelfTest = Assert<
    Equals<KindMismatch<{ narrow: string }, { narrow: string | null }>, 'narrow'> extends true
        ? Equals<KindMismatch<{ wide: string | null }, { wide: string }>, 'wide'>
        : false
>;

// `AgentInfo` also carries the Hub catalog's fields, which come from
// `merge_with_registry` rather than a pydantic model; each one is checked
// against its real emitter by tests/unit/test_webui_agent_info_contract.py.
type CatalogOnlyAgentInfoFields =
    | 'deprecated'
    | 'download_size_bytes'
    | 'eval_score'
    | 'eval_score_version'
    | 'eval_scorecard_url'
    | 'installed_version'
    | 'latest_version'
    | 'permissions'
    | 'requirements'
    | 'requires_trust'
    | 'security_tier'
    | 'status'
    | 'type'
    | 'version';

export type ApiContract = [
    NoDrift<Drift<Api.AgentInfo, AgentInfo, CatalogOnlyAgentInfoFields>>,
    NoDrift<Drift<Api.BrowseResponse, BrowseResponse>>,
    NoDrift<Drift<Api.DiskAgentInfo, DiskAgentInfo>>,
    NoDrift<Drift<Api.DocumentResponse, Document>>,
    NoDrift<Drift<Api.DownloadProgress, DownloadProgress>>,
    NoDrift<Drift<Api.FileEntry, FileEntry>>,
    NoDrift<Drift<Api.IndexFolderResponse, IndexFolderResponse>>,
    NoDrift<Drift<Api.InferenceStatsResponse, InferenceStats>>,
    NoDrift<Drift<Api.ModelStatus, ModelStatus>>,
    NoDrift<Drift<Api.OnboardingStatus, OnboardingStatusResponse>>,
    NoDrift<Drift<Api.ParseScheduleResponse, ParsedSchedule>>,
    NoDrift<Drift<Api.PreflightReport, PreflightReport>>,
    NoDrift<Drift<Api.QuickLink, QuickLink>>,
    NoDrift<Drift<Api.ScheduleResponse, Schedule>>,
    NoDrift<Drift<Api.SessionResponse, Session>>,
    NoDrift<Drift<Api.SettingsResponse, Settings>>,
    NoDrift<Drift<Api.SourceInfo, SourceInfo>>,
    NoDrift<Drift<Api.SystemStatus, SystemStatus>>,
];
