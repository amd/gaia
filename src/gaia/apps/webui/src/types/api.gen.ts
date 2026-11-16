// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// AUTO-GENERATED from the Agent UI backend's pydantic models — do not edit.
// Regenerate from the repo root: python -m gaia.ui.export_openapi && (cd src/gaia/apps/webui && npm run gen:api-types)
// Field names come straight from the wire; response fields with a backend
// default are optional here even though the server always sends them.

export interface ApiSchemas {
  ActivationRequest: ActivationRequest;
  ActivationSaved: ActivationSaved;
  ActiveModelResponse: ActiveModelResponse;
  ActiveRunsResponse: ActiveRunsResponse;
  ActiveSkill: ActiveSkill;
  AdminClearRequest: AdminClearRequest;
  AdminSeedItem: AdminSeedItem;
  AdminSeedRequest: AdminSeedRequest;
  AgentCapabilities: AgentCapabilities;
  AgentInfo: AgentInfo;
  AgentListResponse: AgentListResponse;
  AgentMcpServer: AgentMcpServer;
  AgentMcpsResponse: AgentMcpsResponse;
  AgentStepResponse: AgentStepResponse;
  AgentTool: AgentTool;
  AgentUiHealthResponse: AgentUiHealthResponse;
  AgentUiHealthStats: AgentUiHealthStats;
  AttachDocumentRequest: AttachDocumentRequest;
  AuthorizeRequest: AuthorizeRequest;
  AutomationList: AutomationList;
  AutomationRow: AutomationRow;
  BackendOption: BackendOption;
  Body_upload_document_blob_api_documents_upload_post: BodyUploadDocumentBlobApiDocumentsUploadPost;
  Body_upload_file_api_files_upload_post: BodyUploadFileApiFilesUploadPost;
  BrowseResponse: BrowseResponse;
  CancelRequest: CancelRequest;
  CancelStreamRequest: CancelStreamRequest;
  CancelStreamResponse: CancelStreamResponse;
  ChangeTotals: ChangeTotals;
  ChatFileChange: ChatFileChange;
  ChatModelRequest: ChatModelRequest;
  ChatRequest: ChatRequest;
  ChatResponse: ChatResponse;
  ChatSpendChat: ChatSpendChat;
  ChatSpendItem: ChatSpendItem;
  ChatSpendReport: ChatSpendReport;
  ChatSpendToday: ChatSpendToday;
  CommandOutputResponse: CommandOutputResponse;
  CommitRequest: CommitRequest;
  ConfigureRequest: ConfigureRequest;
  ConfirmToolResponse: ConfirmToolResponse;
  ConnectRequest: ConnectRequest;
  ConnectorActivationsResponse: ConnectorActivationsResponse;
  ConnectorAuthorizeResponse: ConnectorAuthorizeResponse;
  ConnectorConfigureResponse: ConnectorConfigureResponse;
  ConnectorDeviceAuthResponse: ConnectorDeviceAuthResponse;
  ConnectorFieldSummary: ConnectorFieldSummary;
  ConnectorGrantsResponse: ConnectorGrantsResponse;
  ConnectorListResponse: ConnectorListResponse;
  ConnectorOAuthClient: ConnectorOAuthClient;
  ConnectorSummary: ConnectorSummary;
  ConnectorTestResponse: ConnectorTestResponse;
  ConnectorUnavailable: ConnectorUnavailable;
  ContextCategory: ContextCategory;
  ContextMeasured: ContextMeasured;
  ContextRequest: ContextRequest;
  ContextState: ContextState;
  ContextUnavailable: ContextUnavailable;
  CpuUsage: CpuUsage;
  CreateScheduleRequest: CreateScheduleRequest;
  CreateSessionRequest: CreateSessionRequest;
  CustomModelAdded: CustomModelAdded;
  CustomModelCheck: CustomModelCheck;
  CustomModelEntry: CustomModelEntry;
  CustomModelRemoved: CustomModelRemoved;
  CustomModelReport: CustomModelReport;
  CustomModelRequest: CustomModelRequest;
  CustomModelVariant: CustomModelVariant;
  DefaultPermissionModeResponse: DefaultPermissionModeResponse;
  DeviceAccessStatus: DeviceAccessStatus;
  DevicePairRequest: DevicePairRequest;
  DevicePairResponse: DevicePairResponse;
  DevicePairingOffer: DevicePairingOffer;
  DeviceUsage: DeviceUsage;
  DiscordConnectRequest: DiscordConnectRequest;
  DiscordConnectResponse: DiscordConnectResponse;
  DiscordConnectResult: DiscordConnectResult;
  DiscordStartRequest: DiscordStartRequest;
  DiscordStatus: DiscordStatus;
  DiscoveryCommit: DiscoveryCommit;
  DiscoveryCommitItem: DiscoveryCommitItem;
  DocumentCancelResponse: DocumentCancelResponse;
  DocumentDeleteResponse: DocumentDeleteResponse;
  DocumentListResponse: DocumentListResponse;
  DocumentResponse: DocumentResponse;
  DocumentStatusResponse: DocumentStatusResponse;
  DocumentUploadRequest: DocumentUploadRequest;
  DownloadModelResponse: DownloadModelResponse;
  DownloadProgress: DownloadProgress;
  EstimatedTokenizer: EstimatedTokenizer;
  EvalMailboxRequest: EvalMailboxRequest;
  ExactTokenizer: ExactTokenizer;
  FailedRedemption: FailedRedemption;
  FileDiff: FileDiff;
  FileEntry: FileEntry;
  FileListResponse: FileListResponse;
  FilePreviewResponse: FilePreviewResponse;
  FileSearchResponse: FileSearchResponse;
  FileSearchResult: FileSearchResult;
  FileStatusEntry: FileStatusEntry;
  FileStatusRequest: FileStatusRequest;
  FileStatusResponse: FileStatusResponse;
  FileUploadResponse: FileUploadResponse;
  FolderCheckResponse: FolderCheckResponse;
  FolderRequest: FolderRequest;
  FollowUpDelivered: FollowUpDelivered;
  FollowUpOutcome: FollowUpOutcome;
  FollowUpRequest: FollowUpRequest;
  ForwardConnectionRequest: ForwardConnectionRequest;
  GitCommitResult: GitCommitResult;
  GitDraft: GitDraft;
  GitPullRequestResult: GitPullRequestResult;
  GitRepo: GitRepo;
  GoalCreate: GoalCreate;
  GoalListResponse: GoalListResponse;
  GoalResponse: GoalResponse;
  GoalStatsResponse: GoalStatsResponse;
  GoalStatusUpdate: GoalStatusUpdate;
  GoalStoreHealthResponse: GoalStoreHealthResponse;
  GoalTaskResponse: GoalTaskResponse;
  GpuMemoryApplyRequest: GpuMemoryApplyRequest;
  GpuMemoryModel: GpuMemoryModel;
  GpuMemoryPending: GpuMemoryPending;
  GpuMemoryPendingRequest: GpuMemoryPendingRequest;
  GpuMemoryPreset: GpuMemoryPreset;
  GpuMemoryRevert: GpuMemoryRevert;
  GpuMemoryRevertRequest: GpuMemoryRevertRequest;
  GpuMemorySetting: GpuMemorySetting;
  GpuMemoryStatusResponse: GpuMemoryStatusResponse;
  GrantRequest: GrantRequest;
  GrantSaved: GrantSaved;
  HTTPValidationError: HTTPValidationError;
  IndexFolderRequest: IndexFolderRequest;
  IndexFolderResponse: IndexFolderResponse;
  IndexProgressResponse: IndexProgressResponse;
  InferenceCommit: InferenceCommit;
  InferenceCommitItem: InferenceCommitItem;
  InferenceStatsResponse: InferenceStatsResponse;
  InitTaskInfo: InitTaskInfo;
  InstallRequest: InstallRequest;
  InternetRequest: InternetRequest;
  JsonExportResponse: JsonExportResponse;
  KeptOriginal: KeptOriginal;
  KeptRetry: KeptRetry;
  KnowledgeCreate: KnowledgeCreate;
  KnowledgeUpdate: KnowledgeUpdate;
  LemonadeDetection: LemonadeDetection;
  LemonadeProbe: LemonadeProbe;
  LemonadeTarget: LemonadeTarget;
  LemonadeTargetSwitched: LemonadeTargetSwitched;
  LimitRequest: LimitRequest;
  LoadModelRequest: LoadModelRequest;
  LoadModelResponse: LoadModelResponse;
  LoadedModelInfo: LoadedModelInfo;
  LocalContextReload: LocalContextReload;
  LocalContextResponse: LocalContextResponse;
  LocalContextView: LocalContextView;
  LocalMemoryView: LocalMemoryView;
  LocalRestartResponse: LocalRestartResponse;
  LocalStatusResponse: LocalStatusResponse;
  LogFileInfo: LogFileInfo;
  LogLineInfo: LogLineInfo;
  LogReadResponse: LogReadResponse;
  LogSourceInfo: LogSourceInfo;
  LogSourcesResponse: LogSourcesResponse;
  MCPServerInfo: MCPServerInfo;
  MCPServerList: MCPServerList;
  MCPServerStatus: MCPServerStatus;
  MCPServerTools: MCPServerTools;
  MCPStatusResponse: MCPStatusResponse;
  MCPToolInfo: MCPToolInfo;
  MarkdownExportResponse: MarkdownExportResponse;
  McpOutputResponse: McpOutputResponse;
  MemoryActivityDay: MemoryActivityDay;
  MemoryClearCounts: MemoryClearCounts;
  MemoryCommitResponse: MemoryCommitResponse;
  MemoryConsolidateResponse: MemoryConsolidateResponse;
  MemoryConversationStats: MemoryConversationStats;
  MemoryConversationTurn: MemoryConversationTurn;
  MemoryEmbeddingCoverage: MemoryEmbeddingCoverage;
  MemoryEntity: MemoryEntity;
  MemoryKnowledgeItem: MemoryKnowledgeItem;
  MemoryKnowledgePage: MemoryKnowledgePage;
  MemoryKnowledgeStats: MemoryKnowledgeStats;
  MemoryKnowledgeWriteResponse: MemoryKnowledgeWriteResponse;
  MemoryProcedureStats: MemoryProcedureStats;
  MemoryPruneResponse: MemoryPruneResponse;
  MemoryRebuildEmbeddingsResponse: MemoryRebuildEmbeddingsResponse;
  MemoryRebuildFtsResponse: MemoryRebuildFtsResponse;
  MemoryReconcileResponse: MemoryReconcileResponse;
  MemoryReinitializeResponse: MemoryReinitializeResponse;
  MemorySession: MemorySession;
  MemorySettingsBody: MemorySettingsBody;
  MemorySettingsResponse: MemorySettingsResponse;
  MemorySettingsUpdateResponse: MemorySettingsUpdateResponse;
  MemoryStatsResponse: MemoryStatsResponse;
  MemorySystemContextRefresh: MemorySystemContextRefresh;
  MemoryTemporalStats: MemoryTemporalStats;
  MemoryToolStats: MemoryToolStats;
  MemoryToolSummary: MemoryToolSummary;
  MemoryUsage: MemoryUsage;
  MessageDeleteResponse: MessageDeleteResponse;
  MessageListResponse: MessageListResponse;
  MessageResponse: MessageResponse;
  MessagesDeleteResponse: MessagesDeleteResponse;
  ModelServerBackendRequest: ModelServerBackendRequest;
  ModelServerInfo: ModelServerInfo;
  ModelStatus: ModelStatus;
  NetworkUsage: NetworkUsage;
  OpenFileRequest: OpenFileRequest;
  OpenFileResponse: OpenFileResponse;
  OutputItem: OutputItem;
  PairedDevice: PairedDevice;
  PairedDeviceList: PairedDeviceList;
  ParseScheduleRequest: ParseScheduleRequest;
  ParseScheduleResponse: ParseScheduleResponse;
  PermissionGrantResponse: PermissionGrantResponse;
  PermissionModeRequest: PermissionModeRequest;
  PermissionsListResponse: PermissionsListResponse;
  PolicyNotice: PolicyNotice;
  PreflightReport: PreflightReport;
  PriceCard: PriceCard;
  PriceListEntry: PriceListEntry;
  PriceListResponse: PriceListResponse;
  ProviderActiveView: ProviderActiveView;
  ProviderConnectResponse: ProviderConnectResponse;
  ProviderKeyForgotten: ProviderKeyForgotten;
  ProviderKeyStoreRetry: ProviderKeyStoreRetry;
  ProviderListResponse: ProviderListResponse;
  ProviderModelEntry: ProviderModelEntry;
  ProviderModelsResponse: ProviderModelsResponse;
  ProviderNetwork: ProviderNetwork;
  ProviderNpuProblem: ProviderNpuProblem;
  ProviderPlacement: ProviderPlacement;
  ProviderRestoreNotice: ProviderRestoreNotice;
  ProviderSummary: ProviderSummary;
  ProviderSummaryList: ProviderSummaryList;
  ProviderSummaryNetwork: ProviderSummaryNetwork;
  ProviderView: ProviderView;
  PullRequestRequest: PullRequestRequest;
  PutAwayContext: PutAwayContext;
  QuickLink: QuickLink;
  RemoveModelRequest: RemoveModelRequest;
  ResourceUsage: ResourceUsage;
  RestartRequest: RestartRequest;
  RetryChoiceRequest: RetryChoiceRequest;
  RetryStats: RetryStats;
  RetryWithRequest: RetryWithRequest;
  RetryWithResponse: RetryWithResponse;
  RunningAnswer: RunningAnswer;
  RunningIndexJob: RunningIndexJob;
  ScheduleDeleteResponse: ScheduleDeleteResponse;
  ScheduleDescription: ScheduleDescription;
  ScheduleListResponse: ScheduleListResponse;
  ScheduleResponse: ScheduleResponse;
  ScheduleResultsResponse: ScheduleResultsResponse;
  ScopeEntry: ScopeEntry;
  ScriptedUserRequest: ScriptedUserRequest;
  SearchSnippet: SearchSnippet;
  SelectModelRequest: SelectModelRequest;
  SessionActivateResponse: SessionActivateResponse;
  SessionAttachResponse: SessionAttachResponse;
  SessionChanges: SessionChanges;
  SessionDeleteResponse: SessionDeleteResponse;
  SessionDetachResponse: SessionDetachResponse;
  SessionEnvironment: SessionEnvironment;
  SessionGit: SessionGit;
  SessionListResponse: SessionListResponse;
  SessionOutputs: SessionOutputs;
  SessionPermissionsResponse: SessionPermissionsResponse;
  SessionPinRequest: SessionPinRequest;
  SessionResponse: SessionResponse;
  SessionSearchResponse: SessionSearchResponse;
  SessionSearchResult: SessionSearchResult;
  SettingsResponse: SettingsResponse;
  SettingsUpdateRequest: SettingsUpdateRequest;
  SetupBackend: SetupBackend;
  SetupCancelResponse: SetupCancelResponse;
  SetupCatalogEntry: SetupCatalogEntry;
  SetupChatModel: SetupChatModel;
  SetupCheckResponse: SetupCheckResponse;
  SetupComponent: SetupComponent;
  SetupDevice: SetupDevice;
  SetupDisk: SetupDisk;
  SetupGaiaBuild: SetupGaiaBuild;
  SetupLemonade: SetupLemonade;
  SetupModelLoad: SetupModelLoad;
  SetupModelNeedResponse: SetupModelNeedResponse;
  SetupModelProgress: SetupModelProgress;
  SetupNeedModel: SetupNeedModel;
  SetupNpuProblem: SetupNpuProblem;
  SetupNpuStatus: SetupNpuStatus;
  SetupOption: SetupOption;
  SetupOverall: SetupOverall;
  SetupPlanModel: SetupPlanModel;
  SetupPlanResponse: SetupPlanResponse;
  SetupProblemView: SetupProblemView;
  SetupRemoveModelResponse: SetupRemoveModelResponse;
  SetupReportResponse: SetupReportResponse;
  SetupRunRequest: SetupRunRequest;
  SetupRunStatus: SetupRunStatus;
  SetupStepView: SetupStepView;
  SetupSystem: SetupSystem;
  SetupTestRow: SetupTestRow;
  SkillFeaturedLibrary: SkillFeaturedLibrary;
  SkillFeaturedResponse: SkillFeaturedResponse;
  SkillLinkAddRequest: SkillLinkAddRequest;
  SkillLinkAddResponse: SkillLinkAddResponse;
  SkillLinkAdded: SkillLinkAdded;
  SkillLinkEntry: SkillLinkEntry;
  SkillLinkPermission: SkillLinkPermission;
  SkillLinkPreviewResponse: SkillLinkPreviewResponse;
  SkillLinkRequest: SkillLinkRequest;
  SkillListEntry: SkillListEntry;
  SkillPreferences: SkillPreferences;
  SkillPreferencesList: SkillPreferencesList;
  SkillPreferencesReset: SkillPreferencesReset;
  SkillRemoved: SkillRemoved;
  SkillsListResponse: SkillsListResponse;
  SkipModelRequest: SkipModelRequest;
  SourceInfo: SourceInfo;
  SpeakRequest: SpeakRequest;
  SpeechModel: SpeechModel;
  SpeechSettings: SpeechSettings;
  SpeechSettingsUpdate: SpeechSettingsUpdate;
  SpendingModelRow: SpendingModelRow;
  SpendingReport: SpendingReport;
  StartCardView: StartCardView;
  StartScreenResponse: StartScreenResponse;
  StartWorkingFolder: StartWorkingFolder;
  SummarizedContext: SummarizedContext;
  SystemStatus: SystemStatus;
  TargetRequest: TargetRequest;
  TaskCreate: TaskCreate;
  TaskStatusUpdate: TaskStatusUpdate;
  ToolConfirmRequest: ToolConfirmRequest;
  ToolLoading: ToolLoading;
  TunnelDevice: TunnelDevice;
  TunnelStatus: TunnelStatus;
  TunnelStopped: TunnelStopped;
  TurnChanges: TurnChanges;
  TurnChangesList: TurnChangesList;
  TurnDiff: TurnDiff;
  TurnFileChange: TurnFileChange;
  TurnRouteResponse: TurnRouteResponse;
  TurnSummaryCost: TurnSummaryCost;
  TurnSummaryDocument: TurnSummaryDocument;
  TurnSummaryFile: TurnSummaryFile;
  TurnSummaryLiveData: TurnSummaryLiveData;
  TurnSummaryModel: TurnSummaryModel;
  TurnSummaryOutput: TurnSummaryOutput;
  TurnSummaryPage: TurnSummaryPage;
  TurnSummaryResponse: TurnSummaryResponse;
  UndoAllResult: UndoAllResult;
  UndoRequest: UndoRequest;
  UndoResult: UndoResult;
  UndoSettings: UndoSettings;
  UndoSettingsResponse: UndoSettingsResponse;
  UnloadModelRequest: UnloadModelRequest;
  UnloadModelResponse: UnloadModelResponse;
  UntrackedWork: UntrackedWork;
  UpdateScheduleRequest: UpdateScheduleRequest;
  UpdateSessionRequest: UpdateSessionRequest;
  UserInputRequest: UserInputRequest;
  UserInputResponse: UserInputResponse;
  ValidationError: ValidationError;
  VisionModelRequest: VisionModelRequest;
  Voice: Voice;
  WarmUpStatus: WarmUpStatus;
  WorkInProgress: WorkInProgress;
  gaia__ui__routers__setup__DownloadModelRequest: Gaia_Ui_Routers_Setup__DownloadModelRequest;
  gaia__ui__routers__system__DownloadModelRequest: Gaia_Ui_Routers_System__DownloadModelRequest;
}
/**
 * Body for ``PUT /api/connectors/{id}/activations/{agent_id}``.
 *
 * Valid only for ``mcp_server`` connectors — see ``_require_mcp_server``.
 *
 * ``scopes`` is optional: when present and no grant exists for the pair,
 * it is used to auto-create the grant (one-click convenience). When the
 * pair already has a grant the body is ignored — see issue #1005.
 */
export interface ActivationRequest {
  scopes?: string[] | null;
}
/**
 * The agent is active on the connector; ``auto_granted`` when a grant was made too.
 */
export interface ActivationSaved {
  active: true;
  agent_id: string;
  auto_granted: boolean;
  connector_id: string;
  [k: string]: unknown | undefined;
}
/**
 * The active model, whether it is downloaded, and the restore notice.
 */
export interface ActiveModelResponse {
  config_error: string | null;
  display_name: string | null;
  downloaded: boolean | null;
  inference_description?: string | null;
  inference_remote?: boolean | null;
  is_default: boolean;
  label: string | null;
  model: string | null;
  provider: "local" | "fireworks" | "amd";
  provider_name: string | null;
  remote: boolean;
  restore: ProviderRestoreNotice;
  [k: string]: unknown | undefined;
}
/**
 * Whether last run's model was restored; ``retry`` is set only on a failure.
 */
export interface ProviderRestoreNotice {
  message: string | null;
  retry?: boolean | null;
  status: "failed" | "none" | "restored" | "pending";
  [k: string]: unknown | undefined;
}
/**
 * Chats with a turn in flight, those waiting in line, and those paused on the user.
 */
export interface ActiveRunsResponse {
  awaiting_user: {
    [k: string]: "permission" | "question" | undefined;
  };
  queued: string[];
  session_ids: string[];
  [k: string]: unknown | undefined;
}
/**
 * A skill loaded in the chat, and how it came to be.
 */
export interface ActiveSkill {
  how: "model" | "auto" | "always";
  name: string;
  [k: string]: unknown | undefined;
}
/**
 * Request body for the eval-only :func:`admin_clear` endpoint.
 *
 * ``scope`` selects which subset of memory to wipe.  Defaults to ``all``.
 */
export interface AdminClearRequest {
  scope?: string;
}
/**
 * One row for the eval-only :func:`admin_seed` endpoint.
 *
 * The shape mirrors :class:`KnowledgeCreate` but adds ``confidence`` and
 * ``source`` because seeding is meant to plant *known* memory state, not
 * user-generated content.  Dedup is bypassed in :meth:`MemoryStore.seed_bulk`
 * so two near-identical items become two rows — the whole point of seeding.
 */
export interface AdminSeedItem {
  category?: string;
  confidence?: number;
  content: string;
  context?: string;
  domain?: string | null;
  due_at?: string | null;
  entity?: string | null;
  sensitive?: boolean;
  source?: string;
}
export interface AdminSeedRequest {
  items: AdminSeedItem[];
}
/**
 * The chat's agent, its tools, and its loaded skills (null when it runs elsewhere).
 */
export interface AgentCapabilities {
  agent_type: string;
  session_id: string;
  skills: ActiveSkill[] | null;
  tools: AgentTool[];
  [k: string]: unknown | undefined;
}
/**
 * One tool the chat's agent can call.
 */
export interface AgentTool {
  category: string;
  description: string;
  name: string;
  [k: string]: unknown | undefined;
}
/**
 * Information about a registered agent.
 */
export interface AgentInfo {
  category?: string;
  consumes_mcp_servers?: boolean;
  conversation_starters?: string[];
  description: string;
  device_configs?: {
    [k: string]: unknown | undefined;
  }[];
  icon?: string;
  id: string;
  language?: string;
  min_memory_gb?: number | null;
  model_tiers?: {
    [k: string]: unknown | undefined;
  }[];
  models?: string[];
  name: string;
  namespaced_agent_id?: string;
  required_connections?: {
    [k: string]: unknown | undefined;
  }[];
  source: "builtin" | "custom_python" | "native" | "installed";
  tags?: string[];
  tools_count?: number;
}
/**
 * List of registered agents.
 */
export interface AgentListResponse {
  agents: AgentInfo[];
  total: number;
}
/**
 * An MCP server a custom agent declares in its own ``mcp_servers.json``.
 */
export interface AgentMcpServer {
  agent_id: string;
  agent_name: string;
  args: string[];
  command: string;
  config_path: string;
  disabled: boolean;
  server_name: string;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``GET /api/connectors/agent-mcps``.
 */
export interface AgentMcpsResponse {
  agent_mcps: AgentMcpServer[];
  [k: string]: unknown | undefined;
}
/**
 * A single step in the agent's execution (persisted).
 */
export interface AgentStepResponse {
  active?: boolean;
  commandOutput?: CommandOutputResponse | null;
  currentStep?: number | null;
  data?: unknown;
  decision?: string | null;
  declined?: boolean | null;
  detail?: string | null;
  fileList?: FileListResponse | null;
  id: number;
  label: string;
  latencyMs?: number | null;
  mcpOutput?: McpOutputResponse | null;
  mcpServer?: string | null;
  mcpServerLabel?: string | null;
  mcpTool?: string | null;
  narration?: string | null;
  needsModel?: string | null;
  planNarrations?: string[] | null;
  planSteps?: string[] | null;
  policyVersion?: string | null;
  reason?: string | null;
  receiptId?: string | null;
  render?: string | null;
  result?: string | null;
  ruleIds?: string[] | null;
  success?: boolean | null;
  timedOut?: boolean | null;
  timestamp?: number;
  tool?: string | null;
  type: string;
}
/**
 * Structured output from a shell command execution.
 */
export interface CommandOutputResponse {
  command?: string;
  cwd?: string | null;
  duration_seconds?: number | null;
  return_code?: number;
  stderr?: string;
  stdout?: string;
  truncated?: boolean;
}
/**
 * Structured file list from file search tool results.
 */
export interface FileListResponse {
  files?: {
    [k: string]: unknown | undefined;
  }[];
  total?: number;
}
/**
 * The text an MCP tool returned, capped for the tool row.
 */
export interface McpOutputResponse {
  text?: string;
  truncated?: boolean;
}
/**
 * The server is up.
 */
export interface AgentUiHealthResponse {
  service: "gaia-agent-ui";
  stats: AgentUiHealthStats;
  status: "ok";
  [k: string]: unknown | undefined;
}
/**
 * Row counts and indexed size in the chat database.
 */
export interface AgentUiHealthStats {
  documents: number;
  messages: number;
  sessions: number;
  total_chunks: number;
  total_size_bytes: number;
  [k: string]: unknown | undefined;
}
/**
 * Request to attach a document to a session.
 */
export interface AttachDocumentRequest {
  document_id: string;
}
export interface AuthorizeRequest {
  grant_agents?: string[];
  scopes?: string[];
}
/**
 * Every automation; ``delivers`` is null when Discord's state can't be read.
 */
export interface AutomationList {
  automations: AutomationRow[];
  delivers: boolean | null;
  [k: string]: unknown | undefined;
}
/**
 * One automation, in raw values the app words and formats itself.
 */
export interface AutomationRow {
  check: string;
  days: string;
  every_seconds: number;
  failures: number;
  heartbeat: boolean;
  id: string;
  kind: string;
  last_run_at: number;
  name: string;
  next_at: number;
  paused: boolean;
  prompt: string;
  quiet: string;
  runs: number;
  say: string;
  [k: string]: unknown | undefined;
}
/**
 * A llama.cpp backend this machine can run, with Lemonade's state for it.
 */
export interface BackendOption {
  name: string;
  state: string;
}
export interface BodyUploadDocumentBlobApiDocumentsUploadPost {
  file: string;
}
export interface BodyUploadFileApiFilesUploadPost {
  file: string;
}
/**
 * Response from the file/folder browse endpoint.
 */
export interface BrowseResponse {
  current_path: string;
  entries: FileEntry[];
  folder_refusal?: string | null;
  parent_path?: string | null;
  quick_links?: QuickLink[];
}
/**
 * A single file or folder entry in a directory listing.
 */
export interface FileEntry {
  extension?: string | null;
  modified?: string | null;
  name: string;
  path: string;
  size?: number;
  /**
   * Either 'file' or 'folder'
   */
  type: string;
}
/**
 * A quick-access link to a common filesystem location.
 */
export interface QuickLink {
  icon?: string;
  name: string;
  path: string;
}
export interface CancelRequest {
  model_id?: string | null;
}
export interface CancelStreamRequest {
  session_id: string;
}
/**
 * The running or queued turn was told to stop.
 */
export interface CancelStreamResponse {
  cancelled: true;
  status: "ok";
  [k: string]: unknown | undefined;
}
export interface ChangeTotals {
  additions: number;
  deletions: number;
  files: number;
  uncounted: number;
  [k: string]: unknown | undefined;
}
/**
 * A file from before the chat's first turn to after its last.
 */
export interface ChatFileChange {
  additions: number | null;
  after_size: number;
  before_size: number;
  binary: boolean;
  change: "edited" | "created" | "deleted";
  changed_since: string | null;
  deletions: number | null;
  message_ids: number[];
  path: string;
  too_large: boolean;
  truncated: boolean;
  unified: string;
  [k: string]: unknown | undefined;
}
/**
 * A chat model GAIA knows, or one already downloaded in Lemonade.
 */
export interface ChatModelRequest {
  acknowledge_no_fit?: boolean;
  model_id: string;
}
/**
 * Request to send a chat message.
 */
export interface ChatRequest {
  agent_type?: string | null;
  document_ids?: string[] | null;
  message: string;
  session_id: string;
  stream?: boolean;
}
/**
 * Response from a chat message.
 */
export interface ChatResponse {
  content: string;
  message_id: number;
  sources?: SourceInfo[];
  tokens?: {
    [k: string]: number | undefined;
  } | null;
}
/**
 * RAG source citation.
 */
export interface SourceInfo {
  chunk: string;
  document_id: string;
  filename: string;
  page?: number | null;
  score: number;
}
/**
 * This chat's cloud answers.
 */
export interface ChatSpendChat {
  answers: number;
  items: ChatSpendItem[];
  unpriced_answers: number;
  usd: number;
  [k: string]: unknown | undefined;
}
/**
 * One answer's cost in a chat.
 */
export interface ChatSpendItem {
  created_at: string;
  kind: string | null;
  message_id: number;
  model: string | null;
  usd: number | null;
  [k: string]: unknown | undefined;
}
/**
 * What this chat's cloud answers cost, and today's across chats.
 */
export interface ChatSpendReport {
  chat: ChatSpendChat;
  model: string | null;
  paid: boolean;
  price_source: string;
  today: ChatSpendToday;
  [k: string]: unknown | undefined;
}
/**
 * Every chat's cloud answers since the start of the viewer's day.
 */
export interface ChatSpendToday {
  answers: number;
  chats: number;
  since: string;
  unpriced_answers: number;
  usd: number;
  [k: string]: unknown | undefined;
}
export interface CommitRequest {
  /**
   * True only after the user saw and confirmed the commit.
   */
  confirmed?: boolean;
  message: string;
  root: string;
}
export interface ConfigureRequest {
  config?: {
    [k: string]: unknown | undefined;
  };
}
/**
 * The answer reached the waiting prompt; ``granted`` is what "always" allowed.
 */
export interface ConfirmToolResponse {
  approved: boolean;
  granted: string | null;
  status: "ok";
  [k: string]: unknown | undefined;
}
/**
 * Register a cloud provider in Lemonade; ``api_key`` blank keeps the current key.
 */
export interface ConnectRequest {
  api_key?: string | null;
  auth_header_name?: string | null;
  auth_header_prefix?: string | null;
  base_url?: string | null;
}
/**
 * Result of ``GET /api/connectors/{id}/activations``: on/off per agent id.
 */
export interface ConnectorActivationsResponse {
  activations: {
    [k: string]: boolean | undefined;
  };
  [k: string]: unknown | undefined;
}
/**
 * Result of ``POST /api/connectors/{id}/authorize``.
 */
export interface ConnectorAuthorizeResponse {
  authorization_url: string;
  flow_id: string;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``POST /api/connectors/{id}/configure``; the keys set depend on the step.
 *
 * ``status``/``connector_id``: OAuth client saved. ``flow_id``/
 * ``authorization_url``: browser sign-in started. ``provider`` through
 * ``connected_at``: sign-in finished. ``configured``/``connector_id``/
 * ``command``/``args``: MCP server written.
 */
export interface ConnectorConfigureResponse {
  account_email?: string | null;
  args?: string[] | null;
  authorization_url?: string | null;
  command?: string | null;
  configured?: boolean | null;
  connected_at?: number | null;
  connector_id?: string | null;
  flow_id?: string | null;
  provider?: string | null;
  scopes?: string[] | null;
  status?: string | null;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``POST /api/connectors/{id}/authorize-device``.
 */
export interface ConnectorDeviceAuthResponse {
  expires_in: number;
  interval: number;
  message: string;
  user_code: string;
  verification_uri: string;
  [k: string]: unknown | undefined;
}
/**
 * One field of a connector's setup or configure form.
 */
export interface ConnectorFieldSummary {
  help_md: string;
  key: string;
  kind: "bool" | "email" | "secret" | "select" | "text" | "textarea" | "url";
  label: string;
  placeholder: string;
  prefix: string;
  required: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``GET /api/connectors/{id}/grants``: scopes per agent id.
 */
export interface ConnectorGrantsResponse {
  grants: {
    [k: string]: string[] | undefined;
  };
  [k: string]: unknown | undefined;
}
/**
 * Result of ``GET /api/connectors``.
 */
export interface ConnectorListResponse {
  connectors: (ConnectorSummary | ConnectorUnavailable)[];
  [k: string]: unknown | undefined;
}
/**
 * A catalog connector merged with its live state.
 */
export interface ConnectorSummary {
  account_id: string | null;
  activations: {
    [k: string]: boolean | undefined;
  };
  available_scopes: string[];
  category: string;
  config_error: string | null;
  config_fields: ConnectorFieldSummary[];
  configurable: boolean;
  configured: boolean;
  default_scopes: string[];
  description: string;
  display_name: string;
  docs_url: string | null;
  enabled: boolean;
  icon: string;
  id: string;
  mcp_env_keys: string[];
  oauth_client: ConnectorOAuthClient | null;
  oauth_setup_fields: ConnectorFieldSummary[];
  product_url: string | null;
  scopes: string[];
  setup_url: string | null;
  supports_device_code: boolean;
  tier: number;
  type: "oauth_pkce" | "mcp_server";
  [k: string]: unknown | undefined;
}
/**
 * Which OAuth client a provider would use; the secret is never included.
 */
export interface ConnectorOAuthClient {
  client_id: string | null;
  has_secret: boolean;
  source: ("keyring" | "env") | null;
  [k: string]: unknown | undefined;
}
/**
 * A catalog connector whose live state could not be read.
 */
export interface ConnectorUnavailable {
  display_name: string;
  error: "unavailable";
  id: string;
  path?: string | null;
  reason?: "activations_unreadable" | null;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``POST /api/connectors/{id}/test``.
 */
export interface ConnectorTestResponse {
  detail: string;
  ok: boolean;
  reason?: ("timeout" | "disabled") | null;
  [k: string]: unknown | undefined;
}
export interface ContextCategory {
  key: string;
  label: string;
  tokens: number;
  [k: string]: unknown | undefined;
}
/**
 * How full the model's context is for the chat's next message, by kind.
 */
export interface ContextMeasured {
  at_risk: string[];
  at_risk_tokens: number;
  available: true;
  categories?: ContextCategory[] | null;
  context_state?: ContextState | null;
  documents: number;
  history: number;
  history_budget: number;
  history_used: number;
  memory?: number | null;
  model?: string | null;
  model_window?: number | null;
  query: number;
  reply_reserve: number;
  skills?: number | null;
  system: number;
  tokenizer?: (ExactTokenizer | EstimatedTokenizer) | null;
  tool_definitions?: number | null;
  tool_loading?: ToolLoading | null;
  tools: number;
  turns_hidden: number;
  turns_in_view: number;
  used: number;
  window: number;
  [k: string]: unknown | undefined;
}
/**
 * What the agent moved out of the window to keep going.
 */
export interface ContextState {
  put_away: PutAwayContext | null;
  summarized: SummarizedContext | null;
  [k: string]: unknown | undefined;
}
export interface PutAwayContext {
  results: number;
  tokens: number;
  [k: string]: unknown | undefined;
}
export interface SummarizedContext {
  summary_tokens: number;
  tokens: number;
  turns: number;
  [k: string]: unknown | undefined;
}
export interface ExactTokenizer {
  exact: true;
  model: string;
  [k: string]: unknown | undefined;
}
export interface EstimatedTokenizer {
  exact: false;
  reason: string;
  [k: string]: unknown | undefined;
}
/**
 * Tools sent with every request, and those loaded only when needed.
 */
export interface ToolLoading {
  loaded: string[];
  on_demand: string[];
  [k: string]: unknown | undefined;
}
/**
 * ``ctx_size`` None returns the model to GAIA's automatic size.
 */
export interface ContextRequest {
  ctx_size?: number | null;
  model: string;
}
/**
 * Why the chat's context can't be measured now.
 */
export interface ContextUnavailable {
  available: false;
  reason: string;
  [k: string]: unknown | undefined;
}
export interface CpuUsage {
  /**
   * Current clock speed, when the OS reports it.
   */
  ghz: number | null;
  /**
   * Percent per sample, oldest first, one per interval_ms.
   */
  history: number[];
  percent: number;
}
/**
 * Request to create a new scheduled task.
 */
export interface CreateScheduleRequest {
  /**
   * Interval string, e.g. 'every 6h', 'every 30m', 'daily'
   */
  interval: string;
  /**
   * Unique name for the scheduled task
   */
  name: string;
  /**
   * The prompt to execute on each run
   */
  prompt: string;
  /**
   * IANA time zone the schedule's times are in, e.g. 'America/Los_Angeles'. Defaults to the server machine's zone.
   */
  timezone?: string | null;
}
/**
 * Request to create a new chat session.
 */
export interface CreateSessionRequest {
  agent_type?: string | null;
  device?: string | null;
  document_ids?: string[];
  folders?: string[];
  mail_provider?: string | null;
  model?: string | null;
  private?: boolean;
  system_prompt?: string | null;
  title?: string | null;
}
/**
 * The saved model and the check that passed it.
 */
export interface CustomModelAdded {
  model: CustomModelEntry;
  report: CustomModelReport;
  [k: string]: unknown | undefined;
}
/**
 * A model the user added from a link, as saved in config.json.
 */
export interface CustomModelEntry {
  added_at: number;
  architecture: string | null;
  checkpoint: string;
  context_length: number | null;
  display_name: string;
  id: string;
  mmproj: string | null;
  reasoning: boolean;
  recipe: string;
  size_gb: number | null;
  source_url: string;
  tool_calling: boolean;
  vision: boolean;
  [k: string]: unknown | undefined;
}
/**
 * What checking a pasted link found; the model fields are set once it is read.
 */
export interface CustomModelReport {
  architecture?: string | null;
  checkpoint?: string | null;
  checks: CustomModelCheck[];
  context_length?: number | null;
  display_name?: string | null;
  error: string | null;
  file?: string | null;
  flm_version?: string | null;
  llamacpp_version?: string | null;
  mmproj?: string | null;
  model_id?: string | null;
  ok: boolean;
  quant?: string | null;
  reasoning?: boolean | null;
  recipe?: string | null;
  repo?: string | null;
  size_gb?: number | null;
  tool_calling?: boolean | null;
  url: string;
  variants: CustomModelVariant[];
  vision?: boolean | null;
  warnings: string[];
  [k: string]: unknown | undefined;
}
/**
 * One thing verified about a pasted link.
 */
export interface CustomModelCheck {
  detail: string;
  id: string;
  label: string;
  ok: boolean;
  [k: string]: unknown | undefined;
}
/**
 * One quantization of a pasted model and whether it fits.
 */
export interface CustomModelVariant {
  file: string;
  fits: boolean;
  quant: string;
  reason: string | null;
  size_gb: number;
  [k: string]: unknown | undefined;
}
/**
 * The removed model, and the config keys that pointed at it and were cleared.
 */
export interface CustomModelRemoved {
  cleared: ("default_model" | "last_model")[];
  display_name: string;
  removed: string;
  [k: string]: unknown | undefined;
}
/**
 * ``quant`` picks a quantization; None lets GAIA choose one that fits.
 */
export interface CustomModelRequest {
  quant?: string | null;
  url: string;
}
/**
 * The mode new chats start in.
 */
export interface DefaultPermissionModeResponse {
  default_mode: "ask" | "full_access";
  [k: string]: unknown | undefined;
}
/**
 * What the PC shows about access from the home network. Carries no secret.
 */
export interface DeviceAccessStatus {
  error_code?: ("port_in_use" | "unsafe_folder" | "certificate" | "start_failed") | null;
  error_detail?: string | null;
  failed_redemptions?: FailedRedemption[];
  idle_expiry_days: number;
  listening: boolean;
  pairing_expires_at?: string | null;
  pairing_open: boolean;
  pending_expiry_minutes: number;
  port: number;
}
/**
 * A pairing attempt that failed, and why; the device saw one refusal.
 */
export interface FailedRedemption {
  address: string;
  at: string;
  reason: "expired" | "used" | "wrong" | "closed" | "rate_limited";
}
/**
 * A device redeeming the invitation it scanned.
 */
export interface DevicePairRequest {
  device_name: string;
  invitation: string;
  platform: "ios" | "ipados" | "android";
}
/**
 * The device's token, returned once. The device waits for Allow on the PC.
 */
export interface DevicePairResponse {
  device_id: string;
  expires_at: string;
  pc_id: string;
  pc_name: string;
  status: "pending" | "allowed";
  token: string;
}
/**
 * What the PC's pairing QR code carries, as compact JSON.
 *
 * Everything a device needs to pair: where the PC listens, the certificate
 * to pin, and a single-use invitation that dies at ``exp``.
 */
export interface DevicePairingOffer {
  /**
   * When the invitation dies, in Unix seconds
   */
  exp: number;
  /**
   * The single-use invitation
   */
  invite: string;
  /**
   * The PC's name, to show on the device
   */
  name: string;
  /**
   * ``https://<home-network IPv4>:<port>`` for each address
   */
  origins: string[];
  /**
   * Stays the same for this PC, across resets
   */
  pc_id: string;
  /**
   * SHA-256 of the listener's certificate, in hex
   */
  pin: string;
  /**
   * Format version
   */
  v: number;
}
export interface DeviceUsage {
  /**
   * Percent per sample, oldest first, one per interval_ms.
   */
  history: number[];
  id: string;
  kind: "gpu" | "npu";
  /**
   * Dedicated plus shared memory in use, when the OS reports it.
   */
  memory_used_bytes: number | null;
  name: string;
  /**
   * Load of the device's busiest engine.
   */
  percent: number;
}
export interface DiscordConnectRequest {
  model?: string | null;
  token: string;
}
/**
 * The stored token's bot, and the bridge's state once it started.
 */
export interface DiscordConnectResponse {
  connect: DiscordConnectResult;
  status: DiscordStatus;
  [k: string]: unknown | undefined;
}
/**
 * What a stored bot token gives the apps to show next.
 */
export interface DiscordConnectResult {
  application_id: string;
  bot_name: string;
  bot_page_url: string;
  bot_public: boolean;
  connected: boolean;
  invite_url: string;
  [k: string]: unknown | undefined;
}
/**
 * Connection, pairing and bridge state, plus every link setup opens.
 */
export interface DiscordStatus {
  always_allowed: string[];
  autostart: boolean;
  bot_name: string;
  bot_page_url: string;
  bot_profile_url: string;
  connected: boolean;
  detail: string;
  discord_installed: boolean;
  invite_url: string;
  model: string;
  owner_name: string;
  paired: boolean;
  pairing_code: string;
  pairing_expires_at: number;
  portal_url: string;
  running: boolean;
  should_offer_setup: boolean;
  state:
    | "stopped"
    | "starting"
    | "pairing"
    | "connected"
    | "taken_over"
    | "token_invalid"
    | "credentials_unavailable"
    | "conflict"
    | "error";
  [k: string]: unknown | undefined;
}
export interface DiscordStartRequest {
  model?: string | null;
  take_over?: boolean;
}
export interface DiscoveryCommit {
  items: DiscoveryCommitItem[];
}
/**
 * One approved discovery finding; KnowledgeCreate caps its category.
 */
export interface DiscoveryCommitItem {
  category?: string;
  confidence?: number;
  content: string;
  context?: string;
  domain?: string | null;
  due_at?: string | null;
  entity?: string | null;
  sensitive?: boolean;
}
/**
 * Confirmation that a document's indexing was cancelled.
 */
export interface DocumentCancelResponse {
  cancelled: boolean;
  id: string;
  [k: string]: unknown | undefined;
}
/**
 * Confirmation that a document was removed from the library.
 */
export interface DocumentDeleteResponse {
  deleted: boolean;
  [k: string]: unknown | undefined;
}
/**
 * List of documents.
 */
export interface DocumentListResponse {
  documents: DocumentResponse[];
  total: number;
  total_chunks: number;
  total_size_bytes: number;
}
/**
 * A document in the library.
 */
export interface DocumentResponse {
  chunk_count: number;
  error_kind?: string | null;
  file_size: number;
  filename: string;
  filepath: string;
  id: string;
  indexed_at: string;
  indexing_status?: string;
  last_accessed_at?: string | null;
  last_error?: string | null;
  sessions_using?: number;
}
/**
 * A document's indexing status.
 */
export interface DocumentStatusResponse {
  chunk_count: number;
  id: string;
  indexing_status: string;
  is_active: boolean;
  progress: IndexProgressResponse | null;
  [k: string]: unknown | undefined;
}
/**
 * How far a running index has got.
 */
export interface IndexProgressResponse {
  done: number;
  phase: "reading" | "embedding";
  total: number | null;
  [k: string]: unknown | undefined;
}
/**
 * Request to index a document by path.
 */
export interface DocumentUploadRequest {
  filepath: string;
}
/**
 * The download started in the background.
 */
export interface DownloadModelResponse {
  model: string;
  status: "downloading";
  [k: string]: unknown | undefined;
}
/**
 * Progress of an in-flight Lemonade model download.
 *
 * Populated from Lemonade's ``POST /v1/pull`` SSE event stream
 * (``event: progress``). The frontend polls this on /api/system/status
 * so the download banner can show real progress instead of a bare spinner.
 *
 * ``state`` values:
 *   - ``starting``    — pull request issued, waiting for first byte
 *   - ``downloading`` — at least one progress event received
 *   - ``complete``    — all files done (transient — entry expires soon)
 *   - ``error``       — Lemonade returned an error event or the stream broke
 */
export interface DownloadProgress {
  downloaded_bytes?: number;
  file?: string | null;
  file_index?: number;
  message?: string | null;
  model_name: string;
  percent?: number;
  state: string;
  total_bytes?: number;
  total_files?: number;
}
export interface EvalMailboxRequest {
  attached: boolean;
}
export interface FileDiff {
  after_size: number;
  before_size: number;
  binary: boolean;
  change: "edited" | "created" | "deleted" | "pending";
  path: string;
  too_large: boolean;
  truncated: boolean;
  unified: string;
  [k: string]: unknown | undefined;
}
/**
 * Response with file content preview.
 */
export interface FilePreviewResponse {
  columns?: string[] | null;
  encoding?: string | null;
  extension: string;
  is_text: boolean;
  modified: string;
  name: string;
  path: string;
  preview_lines?: string[];
  row_count?: number | null;
  size: number;
  size_display: string;
  total_lines?: number | null;
}
/**
 * Response from file search.
 */
export interface FileSearchResponse {
  query: string;
  results: FileSearchResult[];
  searched_locations?: string[];
  total: number;
  truncated?: boolean;
}
/**
 * A single file search result.
 */
export interface FileSearchResult {
  directory: string;
  extension: string;
  modified: string;
  name: string;
  path: string;
  size: number;
  size_display: string;
}
/**
 * Whether a file exists now, and when it last changed.
 */
export interface FileStatusEntry {
  exists?: boolean | null;
  mtime?: number | null;
  path: string;
}
/**
 * Paths whose current state the side rail re-checks.
 */
export interface FileStatusRequest {
  /**
   * @maxItems 50
   */
  paths: string[];
}
/**
 * Current state of each requested path, in request order.
 */
export interface FileStatusResponse {
  files: FileStatusEntry[];
}
/**
 * Response from a file upload.
 */
export interface FileUploadResponse {
  content_type: string;
  filename: string;
  is_image: boolean;
  original_name: string;
  size: number;
  url: string;
}
/**
 * Why a chat can't be given the folder, or null when it can.
 */
export interface FolderCheckResponse {
  refusal: string | null;
  [k: string]: unknown | undefined;
}
export interface FolderRequest {
  /**
   * An absolute folder path.
   */
  path: string;
}
/**
 * The running turn took the message.
 */
export interface FollowUpDelivered {
  delivered: true;
  [k: string]: unknown | undefined;
}
/**
 * What the last finished turn did with the messages added to it.
 */
export interface FollowUpOutcome {
  returned: string[];
  taken: string[];
  [k: string]: unknown | undefined;
}
export interface FollowUpRequest {
  session_id: string;
  text: string;
}
/**
 * Body for ``POST /v1/connections/{provider}`` (#1292).
 *
 * A host app forwards the OAuth client it authenticated the user under
 * (``client_id`` + ``client_secret``) plus the user's ``refresh_token``.
 * GAIA persists both and refreshes AS THE HOST APP'S CLIENT — no second
 * OAuth, no consent step.
 *
 * ``refresh_token`` / ``client_secret`` are secret INPUTS — they are never
 * echoed back in any response. ``account_email`` is display-only in v1
 * (the keyring slot is single-account per provider). ``grant_agents`` is
 * the list of namespaced agent ids (e.g. ``installed:email``) to grant the
 * forwarded scopes so they can resolve the connection ambiently.
 */
export interface ForwardConnectionRequest {
  account_email?: string;
  client_id: string;
  client_secret?: string;
  grant_agents?: string[];
  refresh_token: string;
  scopes?: string[];
}
export interface GitCommitResult {
  commit: string;
  files: string[];
  ignored: string[];
  root: string;
  [k: string]: unknown | undefined;
}
export interface GitDraft {
  body: string;
  title: string;
  [k: string]: unknown | undefined;
}
export interface GitPullRequestResult {
  root: string;
  url: string;
  [k: string]: unknown | undefined;
}
/**
 * A repository holding the chat's changes, with a drafted message.
 */
export interface GitRepo {
  branch: string;
  default_branch: string | null;
  draft: GitDraft;
  files: string[];
  gh: boolean;
  ignored: string[];
  pr_blocked: string | null;
  remote: string | null;
  root: string;
  uncommitted: string[];
  [k: string]: unknown | undefined;
}
export interface GoalCreate {
  description: string;
  mode_required?: string;
  priority?: string;
  title: string;
}
/**
 * Goals matching a listing, with their count.
 */
export interface GoalListResponse {
  goals: GoalResponse[];
  total: number;
  [k: string]: unknown | undefined;
}
/**
 * A goal with its tasks.
 */
export interface GoalResponse {
  approved_for_auto: boolean;
  created_at: string;
  description: string;
  id: string;
  mode_required: "manual" | "goal_driven" | "autonomous";
  priority: "low" | "medium" | "high";
  progress_notes: string;
  source: "user" | "agent_inferred" | "agent_scheduled";
  status: "pending_approval" | "queued" | "in_progress" | "completed" | "failed" | "rejected" | "cancelled";
  tasks: GoalTaskResponse[];
  title: string;
  updated_at: string;
  [k: string]: unknown | undefined;
}
/**
 * One task of a goal.
 */
export interface GoalTaskResponse {
  created_at: string;
  description: string;
  goal_id: string;
  id: string;
  order_index: number;
  result: string | null;
  status: "queued" | "in_progress" | "completed" | "failed" | "blocked" | "cancelled";
  updated_at: string;
  [k: string]: unknown | undefined;
}
/**
 * How many goals and tasks are in each status.
 */
export interface GoalStatsResponse {
  goals: {
    [k: string]: number | undefined;
  };
  tasks: {
    [k: string]: number | undefined;
  };
  [k: string]: unknown | undefined;
}
export interface GoalStatusUpdate {
  progress_notes?: string | null;
  status: string;
}
/**
 * Why the background loop could not read the goal store, or null.
 */
export interface GoalStoreHealthResponse {
  error: string | null;
  [k: string]: unknown | undefined;
}
export interface GpuMemoryApplyRequest {
  confirm?: boolean;
  /**
   * GPU memory to set, in decimal GB (10**9 bytes).
   */
  setting_gb: number;
}
/**
 * A model GAIA could run, and the share it needs.
 */
export interface GpuMemoryModel {
  default: boolean;
  fits_at_setting_gb: number | null;
  fits_now: boolean;
  id: string;
  need_gb: number;
  size_gb: number;
  [k: string]: unknown | undefined;
}
/**
 * A change waiting for a restart, or checked after one.
 */
export interface GpuMemoryPending {
  previous_setting_gb: number | null;
  recorded_at: string | null;
  setting_gb: number;
  source: ("apply" | "revert" | "guided") | null;
  state: "restart_required" | "took_effect" | "not_applied";
  [k: string]: unknown | undefined;
}
export interface GpuMemoryPendingRequest {
  /**
   * GPU memory to set, in decimal GB (10**9 bytes).
   */
  setting_gb: number;
}
/**
 * A share of memory to offer, and the models it unlocks.
 */
export interface GpuMemoryPreset {
  gpu_gb: number;
  setting_gb: number;
  unlocks: string[];
  [k: string]: unknown | undefined;
}
/**
 * Whether GAIA can undo its own change.
 */
export interface GpuMemoryRevert {
  available: boolean;
  previous_setting_gb: number | null;
  [k: string]: unknown | undefined;
}
export interface GpuMemoryRevertRequest {
  confirm?: boolean;
}
/**
 * The knob the user changes, in decimal GB.
 */
export interface GpuMemorySetting {
  current_gb: number;
  max_gb: number;
  min_gb: number;
  name: string;
  [k: string]: unknown | undefined;
}
/**
 * The GPU memory split. A PC the step doesn't apply to sends only the first six fields.
 */
export interface GpuMemoryStatusResponse {
  applies: boolean;
  cpu_gb?: number | null;
  gpu_gb?: number | null;
  guided_steps?: string[] | null;
  guided_value?: string | null;
  hidden_reason: string | null;
  method: "apply" | "guided" | "unsupported";
  method_reason: string;
  models?: GpuMemoryModel[] | null;
  needs_admin?: boolean | null;
  needs_restart?: boolean | null;
  os: string;
  pending?: GpuMemoryPending | null;
  presets?: GpuMemoryPreset[] | null;
  recommended_setting_gb?: number | null;
  reserve_gb?: number | null;
  revert?: GpuMemoryRevert | null;
  setting?: GpuMemorySetting | null;
  show_in_setup: boolean;
  total_gb?: number | null;
  [k: string]: unknown | undefined;
}
export interface GrantRequest {
  scopes?: string[];
}
/**
 * The agent's grant on the connector, as saved.
 */
export interface GrantSaved {
  agent_id: string;
  connector_id: string;
  scopes: string[];
  [k: string]: unknown | undefined;
}
export interface HTTPValidationError {
  detail?: ValidationError[];
}
export interface ValidationError {
  ctx?: {};
  input?: unknown;
  loc: (string | number)[];
  msg: string;
  type: string;
}
/**
 * Request to index all supported documents in a folder.
 */
export interface IndexFolderRequest {
  folder_path: string;
  recursive?: boolean;
}
/**
 * Response from folder indexing operation.
 */
export interface IndexFolderResponse {
  documents?: DocumentResponse[];
  errors?: string[];
  failed?: number;
  indexed?: number;
  skipped?: number;
  skipped_images?: number;
}
export interface InferenceCommit {
  insights: InferenceCommitItem[];
}
/**
 * One approved inference insight. Always stored as a global ``profile`` row.
 */
export interface InferenceCommitItem {
  category?: "profile";
  confidence?: number;
  content: string;
  context?: string;
  domain?: string | null;
  due_at?: string | null;
  entity?: string | null;
  sensitive?: boolean;
}
/**
 * LLM inference performance metrics for a message.
 */
export interface InferenceStatsResponse {
  input_tokens?: number;
  model?: string | null;
  output_tokens?: number;
  place?: string | null;
  retried?: boolean | null;
  route?: TurnRouteResponse | null;
  seconds?: number | null;
  steps?: number | null;
  time_to_first_token?: number;
  tokens_per_second?: number;
}
/**
 * The model a ``hybrid`` turn was routed to, and why.
 */
export interface TurnRouteResponse {
  degraded?: boolean;
  model: string;
  notice?: string;
  reason?: string;
}
/**
 * Summary of a boot-time initialization task (embedded in SystemStatus).
 */
export interface InitTaskInfo {
  name: string;
  status: string;
  step?: string | null;
}
/**
 * Body for ``POST /api/agents/install``.
 */
export interface InstallRequest {
  id: string;
  trust_native?: boolean;
  version?: string | null;
}
export interface InternetRequest {
  on: boolean;
}
/**
 * The session's row and messages as the database stores them.
 */
export interface JsonExportResponse {
  format: "json";
  messages: {
    [k: string]: unknown | undefined;
  }[];
  session: {
    [k: string]: unknown | undefined;
  };
  [k: string]: unknown | undefined;
}
export interface KeptOriginal {
  kept: "original";
  [k: string]: unknown | undefined;
}
/**
 * The retried answer replaced the original.
 */
export interface KeptRetry {
  content: string;
  kept: "retry";
  stats: {
    [k: string]: unknown | undefined;
  };
  [k: string]: unknown | undefined;
}
export interface KnowledgeCreate {
  category?: string;
  content: string;
  context?: string;
  domain?: string | null;
  due_at?: string | null;
  entity?: string | null;
  sensitive?: boolean;
}
export interface KnowledgeUpdate {
  category?: string | null;
  content?: string | null;
  context?: string | null;
  domain?: string | null;
  due_at?: string | null;
  entity?: string | null;
  reminded_at?: string | null;
  sensitive?: boolean | null;
}
/**
 * A Lemonade found on the default ports, and the ports that didn't answer in time.
 */
export interface LemonadeDetection {
  found: LemonadeProbe | null;
  unchecked: string[];
  [k: string]: unknown | undefined;
}
/**
 * What answered at a URL, and whether GAIA can use it.
 */
export interface LemonadeProbe {
  message: string;
  minimum: string | null;
  status: "ok" | "needs_key" | "bad_key" | "too_old" | "not_lemonade" | "unreachable";
  url: string;
  version: string | null;
  [k: string]: unknown | undefined;
}
/**
 * The Lemonade GAIA uses now, who chose it, and whether to offer a switch.
 */
export interface LemonadeTarget {
  api_key_stored: boolean;
  base_url: string | null;
  env_override: string | null;
  env_url_ignored: string | null;
  gaia_installed: boolean;
  mode: "gaia" | "external";
  offer: boolean;
  saved_base_url: string | null;
  saved_mode: string | null;
  source: "env" | "config" | "gaia";
  what_gaia_does: string;
  [k: string]: unknown | undefined;
}
/**
 * The new target, and what the daemon did (None when none runs).
 */
export interface LemonadeTargetSwitched {
  api_key_stored: boolean;
  base_url: string | null;
  daemon: {
    [k: string]: unknown | undefined;
  } | null;
  env_override: string | null;
  env_url_ignored: string | null;
  gaia_installed: boolean;
  mode: "gaia" | "external";
  offer: boolean;
  saved_base_url: string | null;
  saved_mode: string | null;
  source: "env" | "config" | "gaia";
  what_gaia_does: string;
  [k: string]: unknown | undefined;
}
/**
 * ``limit_usd`` None removes the limit.
 */
export interface LimitRequest {
  limit_usd?: number | null;
}
export interface LoadModelRequest {
  ctx_size?: number | null;
  force?: boolean;
  model_name: string;
}
/**
 * The load started in the background.
 */
export interface LoadModelResponse {
  ctx_size: number;
  model: string;
  status: "loading";
  [k: string]: unknown | undefined;
}
/**
 * One model Lemonade has loaded, as the Model server panel shows it.
 */
export interface LoadedModelInfo {
  backend?: string | null;
  backend_source?: string | null;
  ctx_size?: number | null;
  device?: string | null;
  model_type?: string | null;
  name?: string | null;
  recipe?: string | null;
}
/**
 * Whether saving a size reloaded the loaded model, and why not.
 */
export interface LocalContextReload {
  reason: ("already_loaded" | "not_gaias_server" | "warm_up" | "busy" | "restarting") | null;
  started: boolean;
  [k: string]: unknown | undefined;
}
/**
 * The saved context view; ``reload`` is None when the model isn't loaded.
 */
export interface LocalContextResponse {
  automatic: number;
  chosen: number | null;
  display_name: string;
  loaded_ctx: number | null;
  max_fit: number | null;
  memory_source: string;
  min: number | null;
  model: string;
  note: string | null;
  options: number[];
  reload: LocalContextReload | null;
  [k: string]: unknown | undefined;
}
/**
 * The context sizes a model may use on this PC, and the one in force.
 */
export interface LocalContextView {
  automatic: number;
  chosen: number | null;
  display_name: string;
  loaded_ctx: number | null;
  max_fit: number | null;
  memory_source: string;
  min: number | null;
  model: string;
  note: string | null;
  options: number[];
  [k: string]: unknown | undefined;
}
/**
 * Total and free memory of the pool models load into, and this model's share.
 */
export interface LocalMemoryView {
  free_gb: number | null;
  model_gb: number | null;
  source: string;
  total_gb: number;
  [k: string]: unknown | undefined;
}
/**
 * A restart was started, and whether it also starts the stopped server.
 */
export interface LocalRestartResponse {
  model: string;
  starting_server: boolean;
  status: "restarting";
  [k: string]: unknown | undefined;
}
/**
 * Live state of the local model server and its chat model.
 */
export interface LocalStatusResponse {
  active_display_name: string | null;
  active_model: string | null;
  active_remote: boolean;
  base_url: string | null;
  busy: boolean;
  busy_reason: ("warm_up" | "request") | null;
  ctx_size: number | null;
  display_name: string | null;
  error: string | null;
  error_detail: string | null;
  memory: LocalMemoryView | null;
  memory_error: string | null;
  model: string | null;
  owner: ("gaia" | "user" | "remote") | null;
  placement: ProviderPlacement | null;
  restart_error: string | null;
  restarting: boolean;
  state: "stopped" | "idle" | "warming" | "loaded";
  [k: string]: unknown | undefined;
}
/**
 * Device and backend a model runs on, reported or expected.
 */
export interface ProviderPlacement {
  backend: string | null;
  device: string | null;
  label: string | null;
  source: ("reported" | "expected") | null;
  warning: string | null;
  [k: string]: unknown | undefined;
}
/**
 * One log file a source offers.
 */
export interface LogFileInfo {
  modified?: number | null;
  name: string;
  path: string;
  size_bytes?: number | null;
}
/**
 * One redacted log line.
 */
export interface LogLineInfo {
  severity?: string | null;
  tag?: string | null;
  text: string;
}
/**
 * A log's recent lines for the viewer, or a reason code when it can't be read.
 */
export interface LogReadResponse {
  available?: boolean;
  base_url?: string | null;
  cursor?: string | null;
  detail?: string | null;
  dropped?: number;
  lines?: LogLineInfo[];
  log_path?: string | null;
  owner?: string | null;
  reason?: string | null;
  reset?: boolean;
  server_answering?: boolean | null;
  source?: string | null;
}
/**
 * A group of logs: model_server | gaia | service | agent | desktop | other.
 */
export interface LogSourceInfo {
  files?: LogFileInfo[];
  id: string;
}
export interface LogSourcesResponse {
  sources: LogSourceInfo[];
}
export interface MCPServerInfo {
  args: string[];
  command: string;
  enabled: boolean;
  env: {
    [k: string]: string | undefined;
  };
  name: string;
}
export interface MCPServerList {
  servers: MCPServerInfo[];
  [k: string]: unknown | undefined;
}
export interface MCPServerStatus {
  connected: boolean;
  error?: string | null;
  name: string;
  tool_count: number;
}
/**
 * The tools an MCP server offers.
 */
export interface MCPServerTools {
  name: string;
  tools: MCPToolInfo[];
  [k: string]: unknown | undefined;
}
export interface MCPToolInfo {
  description: string;
  name: string;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``GET /api/mcp/status``.
 */
export interface MCPStatusResponse {
  servers: MCPServerStatus[];
  [k: string]: unknown | undefined;
}
/**
 * The session as one markdown document.
 */
export interface MarkdownExportResponse {
  content: string;
  format: "markdown";
  [k: string]: unknown | undefined;
}
/**
 * One day of the activity chart.
 */
export interface MemoryActivityDay {
  conversations: number;
  date: string;
  errors: number;
  knowledge_added: number;
  tool_calls: number;
  [k: string]: unknown | undefined;
}
/**
 * Rows deleted per table.
 */
export interface MemoryClearCounts {
  conversations: number;
  knowledge: number;
  tool_history: number;
  [k: string]: unknown | undefined;
}
/**
 * How many approved items were stored.
 */
export interface MemoryCommitResponse {
  stored: number;
  [k: string]: unknown | undefined;
}
/**
 * Counts from a consolidation run.
 */
export interface MemoryConsolidateResponse {
  consolidated: number;
  extracted_items: number;
  windows: number;
  [k: string]: unknown | undefined;
}
/**
 * Conversation-table aggregates.
 */
export interface MemoryConversationStats {
  first_session: string | null;
  last_session: string | null;
  total_sessions: number;
  total_turns: number;
  [k: string]: unknown | undefined;
}
/**
 * One stored conversation turn.
 */
export interface MemoryConversationTurn {
  content: string;
  context: string | null;
  id: number;
  role: string;
  session_id: string;
  timestamp: string;
  [k: string]: unknown | undefined;
}
/**
 * Share of active knowledge rows that have an embedding.
 */
export interface MemoryEmbeddingCoverage {
  coverage_pct: number;
  total_items: number;
  with_embedding: number;
  without_embedding: number;
  [k: string]: unknown | undefined;
}
/**
 * An entity with how many rows mention it.
 */
export interface MemoryEntity {
  count: number;
  entity: string;
  last_updated: string;
  [k: string]: unknown | undefined;
}
/**
 * One knowledge row (the embedding BLOB is never sent).
 */
export interface MemoryKnowledgeItem {
  category: string;
  confidence: number;
  content: string;
  context: string;
  created_at: string;
  domain: string | null;
  due_at: string | null;
  entity: string | null;
  id: string;
  last_used: string | null;
  metadata: unknown;
  reminded_at: string | null;
  sensitive: boolean;
  source: string;
  superseded_by: string | null;
  updated_at: string;
  use_count: number;
  [k: string]: unknown | undefined;
}
/**
 * A page of knowledge rows.
 */
export interface MemoryKnowledgePage {
  items: MemoryKnowledgeItem[];
  limit: number;
  offset: number;
  total: number;
  [k: string]: unknown | undefined;
}
/**
 * Knowledge-table aggregates for the dashboard header.
 */
export interface MemoryKnowledgeStats {
  avg_confidence: number;
  by_category: {
    [k: string]: number | undefined;
  };
  by_context: {
    [k: string]: number | undefined;
  };
  entity_count: number;
  newest: string | null;
  oldest: string | null;
  sensitive_count: number;
  total: number;
  total_retrievals: number;
  [k: string]: unknown | undefined;
}
/**
 * Result of creating, editing or deleting a knowledge row.
 */
export interface MemoryKnowledgeWriteResponse {
  embed_error?: string | null;
  embedded?: boolean | null;
  knowledge_id: string;
  knowledge_ids?: string[] | null;
  status: "created" | "updated" | "deleted";
  [k: string]: unknown | undefined;
}
/**
 * Procedural-memory aggregates.
 */
export interface MemoryProcedureStats {
  active: number;
  last_recalled: string | null;
  total: number;
  [k: string]: unknown | undefined;
}
/**
 * Rows pruned per table, and conversation turns kept for pending summaries.
 */
export interface MemoryPruneResponse {
  conversations_deleted: number;
  conversations_retained: number;
  knowledge_deleted: number;
  tool_history_deleted: number;
  [k: string]: unknown | undefined;
}
/**
 * Counts from an embedding backfill.
 */
export interface MemoryRebuildEmbeddingsResponse {
  backfilled: number;
  total_without: number;
  [k: string]: unknown | undefined;
}
/**
 * Result of an FTS rebuild.
 */
export interface MemoryRebuildFtsResponse {
  status: "rebuilt";
  [k: string]: unknown | undefined;
}
/**
 * Counts from a reconciliation run.
 */
export interface MemoryReconcileResponse {
  contradicted: number;
  neutral: number;
  pairs_checked: number;
  reinforced: number;
  weakened: number;
  [k: string]: unknown | undefined;
}
/**
 * Result of wiping memory and re-running discovery.
 */
export interface MemoryReinitializeResponse {
  cleared: MemoryClearCounts;
  system_context: MemorySystemContextRefresh;
  [k: string]: unknown | undefined;
}
/**
 * Result of a system-context refresh; ``error`` only when discovery failed.
 */
export interface MemorySystemContextRefresh {
  error?: string | null;
  reason?: string | null;
  skipped?: boolean | null;
  stored: number;
  [k: string]: unknown | undefined;
}
/**
 * A conversation session summary.
 */
export interface MemorySession {
  first_message: string;
  last_activity: string;
  session_id: string;
  started_at: string;
  turn_count: number;
  [k: string]: unknown | undefined;
}
/**
 * Typed request body for PUT /api/memory/settings.
 */
export interface MemorySettingsBody {
  mcp_memory_enabled?: boolean | null;
  memory_enabled?: boolean | null;
  system_discovery_consent?: boolean | null;
}
/**
 * Memory feature settings.
 */
export interface MemorySettingsResponse {
  mcp_memory_enabled: boolean;
  memory_enabled: boolean;
  system_discovery_consent: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Settings after an update, plus the consent-triggered refresh outcome.
 */
export interface MemorySettingsUpdateResponse {
  mcp_memory_enabled: boolean;
  memory_enabled: boolean;
  system_context_error?: string | null;
  system_context_refresh?: MemorySystemContextRefresh | null;
  system_discovery_consent: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Memory dashboard header stats.
 */
export interface MemoryStatsResponse {
  conversations: MemoryConversationStats;
  db_size_bytes: number;
  embedding: MemoryEmbeddingCoverage | null;
  embedding_error?: string | null;
  knowledge: MemoryKnowledgeStats;
  procedures: MemoryProcedureStats;
  temporal: MemoryTemporalStats;
  tools: MemoryToolStats;
  [k: string]: unknown | undefined;
}
/**
 * Counts of time-sensitive knowledge rows.
 */
export interface MemoryTemporalStats {
  overdue_count: number;
  upcoming_count: number;
  [k: string]: unknown | undefined;
}
/**
 * Tool-history aggregates.
 */
export interface MemoryToolStats {
  denied_calls: number;
  overall_success_rate: number;
  total_calls: number;
  total_errors: number;
  unique_tools: number;
  [k: string]: unknown | undefined;
}
/**
 * Per-tool performance stats.
 */
export interface MemoryToolSummary {
  avg_duration_ms: number | null;
  denied_count: number;
  failure_count: number;
  last_error: string | null;
  last_used: string;
  success_count: number;
  success_rate: number;
  tool_name: string;
  total_calls: number;
  [k: string]: unknown | undefined;
}
export interface MemoryUsage {
  /**
   * Percent per sample, oldest first, one per interval_ms.
   */
  history: number[];
  percent: number;
  total_bytes: number;
  used_bytes: number;
}
/**
 * Confirmation that a message was deleted.
 */
export interface MessageDeleteResponse {
  deleted: true;
  [k: string]: unknown | undefined;
}
/**
 * List of messages for a session.
 */
export interface MessageListResponse {
  messages: MessageResponse[];
  total: number;
}
/**
 * A single message.
 */
export interface MessageResponse {
  agent_steps?: AgentStepResponse[] | null;
  content: string;
  created_at: string;
  id: number;
  library_notices?: string[] | null;
  rag_sources?: SourceInfo[] | null;
  role: string;
  session_id: string;
  stats?: InferenceStatsResponse | null;
  stopped?: boolean;
  turn_summary?: TurnSummaryResponse | null;
}
/**
 * Sources, files and data flow of one turn, for the side rail (#5222).
 */
export interface TurnSummaryResponse {
  cost: TurnSummaryCost;
  documents?: TurnSummaryDocument[];
  error?: string | null;
  files?: TurnSummaryFile[];
  live_data?: TurnSummaryLiveData;
  model?: TurnSummaryModel | null;
  outputs?: TurnSummaryOutput[];
  pages?: TurnSummaryPage[];
  sites?: string[];
}
/**
 * What the turn cost.
 */
export interface TurnSummaryCost {
  kind: string;
  usd?: number | null;
}
/**
 * A document the turn's answer drew passages from.
 */
export interface TurnSummaryDocument {
  name: string;
  pages?: number[];
  passages?: number;
  path: string;
}
/**
 * A file the turn created or changed.
 */
export interface TurnSummaryFile {
  mtime?: number | null;
  name: string;
  openable?: boolean;
  path: string;
}
/**
 * How the turn's internet tools fared.
 */
export interface TurnSummaryLiveData {
  attempted?: number;
  failed?: number;
}
/**
 * Where the turn's model ran.
 */
export interface TurnSummaryModel {
  name: string;
  provider: string;
  provider_name: string;
  remote: boolean;
}
/**
 * Something the turn made besides a file: an image, an indexed document,
 * a memory or a scheduled task.
 */
export interface TurnSummaryOutput {
  detail?: string | null;
  kind: string;
  name: string;
  path?: string | null;
  ref?: string | null;
}
/**
 * A web page the turn read, or a search hit when it read none.
 */
export interface TurnSummaryPage {
  kind: string;
  title?: string | null;
  url: string;
}
/**
 * Confirmation that a message and every later one were deleted.
 */
export interface MessagesDeleteResponse {
  count: number;
  deleted: true;
  [k: string]: unknown | undefined;
}
/**
 * Choose the llama.cpp backend for chat models: ``auto`` or a backend name.
 */
export interface ModelServerBackendRequest {
  backend: string;
}
/**
 * Which Lemonade GAIA talks to and what it runs (Settings → Advanced).
 */
export interface ModelServerInfo {
  available_backends?: BackendOption[];
  base_url: string;
  chat_model?: LoadedModelInfo | null;
  default_backend?: string | null;
  embedding_model?: LoadedModelInfo | null;
  embedding_note?: string | null;
  error?: string | null;
  loaded_models?: LoadedModelInfo[];
  owner: string;
  port?: number | null;
  preferred_backend?: string | null;
  reachable?: boolean;
  system_info_error?: string | null;
  version?: string | null;
}
/**
 * Status of a custom model on the Lemonade server.
 */
export interface ModelStatus {
  checked?: boolean;
  downloaded?: boolean;
  found?: boolean;
  loaded?: boolean;
}
export interface NetworkUsage {
  /**
   * The adapter's hardware name.
   */
  description: string;
  id: string;
  kind: "ethernet" | "wifi" | "network";
  /**
   * The connection's name, such as "Ethernet 3".
   */
  name: string;
  /**
   * Bits per second received.
   */
  recv_bps: number;
  /**
   * Bits per second, oldest first.
   */
  recv_history: number[];
  /**
   * Bits per second sent.
   */
  send_bps: number;
  /**
   * Bits per second, oldest first.
   */
  send_history: number[];
}
/**
 * Request to open a file or folder in the system file explorer.
 */
export interface OpenFileRequest {
  launch?: boolean;
  path: string;
  reveal?: boolean;
}
/**
 * The resolved path that was opened.
 */
export interface OpenFileResponse {
  path: string;
  status: "ok";
  [k: string]: unknown | undefined;
}
/**
 * Something the chat produced, tied to the answer that made it.
 */
export interface OutputItem {
  change: ("edited" | "created" | "deleted") | null;
  created_at: string | null;
  detail: string | null;
  id: string;
  kind: "file" | "export" | "image" | "document" | "memory" | "schedule";
  message_id: number | null;
  name: string;
  openable: boolean;
  path: string | null;
  revealable: boolean;
  state: "present" | "undone" | "gone";
  [k: string]: unknown | undefined;
}
/**
 * A paired device, as the PC lists it. Never carries a token or its hash.
 */
export interface PairedDevice {
  created_at: string;
  expires_at?: string | null;
  id: string;
  last_seen_at?: string | null;
  name: string;
  platform: "ios" | "ipados" | "android";
  status: "pending" | "allowed";
}
/**
 * Every paired device.
 */
export interface PairedDeviceList {
  devices: PairedDevice[];
}
/**
 * Request to parse a natural language schedule description.
 */
export interface ParseScheduleRequest {
  /**
   * Natural language schedule description
   */
  input: string;
  /**
   * IANA time zone the schedule's times are in, e.g. 'America/Los_Angeles'. Defaults to the server machine's zone.
   */
  timezone?: string | null;
}
/**
 * Parsed schedule configuration.
 */
export interface ParseScheduleResponse {
  days_of_week?: number[] | null;
  description: string;
  description_parts?: ScheduleDescription | null;
  end_hour?: number | null;
  interval_seconds: number;
  next_run_at?: string | null;
  start_hour?: number | null;
  time_of_day?: string | null;
  timezone?: string | null;
  valid: boolean;
}
/**
 * When a schedule runs, as data the UI words in its own language.
 *
 * ``every``: count + unit, optional days. ``window``: that plus start/end
 * hour. ``days``: named days, optional time. ``daily``: a time each day.
 */
export interface ScheduleDescription {
  count?: number | null;
  /**
   * 0=Monday … 6=Sunday
   */
  days?: number[] | null;
  end_hour?: number | null;
  kind: "every" | "window" | "days" | "daily";
  start_hour?: number | null;
  /**
   * "HH:MM", 24-hour
   */
  time?: string | null;
  unit?: ("second" | "minute" | "hour" | "day" | "week") | null;
}
/**
 * One "always allow" grant: its key and the label shown for it.
 */
export interface PermissionGrantResponse {
  key: string;
  label: string;
  [k: string]: unknown | undefined;
}
/**
 * ``ask`` prompts for every gated tool; ``full_access`` runs them unasked.
 */
export interface PermissionModeRequest {
  mode: string;
}
/**
 * The default mode for new chats and every chat with non-default permissions.
 */
export interface PermissionsListResponse {
  default_mode: "ask" | "full_access";
  sessions: SessionPermissionsResponse[];
  [k: string]: unknown | undefined;
}
/**
 * A chat's permission mode and its "always allow" grants.
 */
export interface SessionPermissionsResponse {
  grants: PermissionGrantResponse[];
  mode: "ask" | "full_access";
  session_id: string;
  [k: string]: unknown | undefined;
}
/**
 * Structured Local only notice translated by the Agent UI.
 */
export interface PolicyNotice {
  cause?: string | null;
  code: string;
  models?: string[] | null;
  reason?: string | null;
  severity: "info" | "warning";
  url?: string | null;
}
/**
 * Result of the first-run hardware scan.
 */
export interface PreflightReport {
  blockers?: string[];
  compatible?: boolean;
  detected_platform?: string | null;
  disk_free_gb?: number | null;
  gpu_name?: string | null;
  gpu_vram_gb?: number | null;
  lemonade_error?: string | null;
  lemonade_running?: boolean;
  npu_detected?: boolean | null;
  os?: string | null;
  ram_gb?: number | null;
  recommended_model?: string;
  recommended_profile?: string;
  required_disk_gb?: number;
  required_memory_gb?: number;
  tier?: string;
  warnings?: string[];
}
/**
 * List price in dollars per million tokens, with where it comes from.
 */
export interface PriceCard {
  cached_input_per_million: number;
  input_per_million: number;
  output_per_million: number;
  source: string;
  [k: string]: unknown | undefined;
}
/**
 * A model's list price before the provider is connected.
 */
export interface PriceListEntry {
  model: string;
  name: string;
  note: string | null;
  price: PriceCard | null;
  rank: number | null;
  [k: string]: unknown | undefined;
}
/**
 * Every priced model a provider offers; ``source`` and ``note`` explain a gap.
 */
export interface PriceListResponse {
  models: PriceListEntry[];
  note: string | null;
  provider: string;
  source: string | null;
  [k: string]: unknown | undefined;
}
/**
 * The model new turns run, and where a new chat would be answered.
 */
export interface ProviderActiveView {
  config_error: string | null;
  display_name: string | null;
  inference_description?: string | null;
  inference_remote?: boolean | null;
  is_default: boolean;
  label: string | null;
  model: string | null;
  provider: "local" | "fireworks" | "amd";
  provider_name: string | null;
  remote: boolean;
  [k: string]: unknown | undefined;
}
/**
 * The connected provider, and whether its key was kept.
 */
export interface ProviderConnectResponse {
  base_url?: string | null;
  blocked?: boolean | null;
  blocked_reason?: string | null;
  env_var?: string | null;
  error?: string | null;
  id: "local" | "fireworks" | "amd";
  key_from_gaia_env?: boolean | null;
  key_hint?: string | null;
  key_message?: string | null;
  key_source?: ("stored" | "environment" | "lemonade") | null;
  key_state?: ("read" | "waiting_for_os_permission" | "store_locked") | null;
  known_models?: boolean | null;
  models_discovered?: boolean | null;
  name: string;
  network?: ProviderNetwork | null;
  privacy_notice: string;
  registered?: boolean | null;
  remember_error: string | null;
  remembered: boolean;
  remote: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Whether the AMD gateway's host can be reached; None when not checked.
 */
export interface ProviderNetwork {
  detail: string | null;
  reachable: boolean | null;
  [k: string]: unknown | undefined;
}
/**
 * Whether a stored key was removed.
 */
export interface ProviderKeyForgotten {
  removed: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Acknowledges that the next key read may ask the OS again.
 */
export interface ProviderKeyStoreRetry {
  retrying: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Every provider with its connection state, and the active model.
 */
export interface ProviderListResponse {
  active: ProviderActiveView;
  lemonade_error: string | null;
  providers: ProviderView[];
  [k: string]: unknown | undefined;
}
/**
 * A provider and its connection state; Local carries only the first four fields.
 */
export interface ProviderView {
  base_url?: string | null;
  blocked?: boolean | null;
  blocked_reason?: string | null;
  env_var?: string | null;
  error?: string | null;
  id: "local" | "fireworks" | "amd";
  key_from_gaia_env?: boolean | null;
  key_hint?: string | null;
  key_message?: string | null;
  key_source?: ("stored" | "environment" | "lemonade") | null;
  key_state?: ("read" | "waiting_for_os_permission" | "store_locked") | null;
  known_models?: boolean | null;
  models_discovered?: boolean | null;
  name: string;
  network?: ProviderNetwork | null;
  privacy_notice: string;
  registered?: boolean | null;
  remote: boolean;
  [k: string]: unknown | undefined;
}
/**
 * A chat model; the local-only fields are absent from cloud lists.
 */
export interface ProviderModelEntry {
  available?: boolean | null;
  context_length: number | null;
  custom: boolean;
  display_name?: string | null;
  download_progress?: DownloadProgress | null;
  downloaded: boolean;
  evidence: string | null;
  fits?: boolean | null;
  id: string;
  labels: string[];
  note: string | null;
  npu_problems?: ProviderNpuProblem[] | null;
  placement?: ProviderPlacement | null;
  price: PriceCard | null;
  rank: number | null;
  recipe: string | null;
  size_gb: number | null;
  source_url: string | null;
  state?: ("loaded" | "downloaded" | "not_downloaded") | null;
  tokens_per_second?: number | null;
  unavailable_reason?: string | null;
  [k: string]: unknown | undefined;
}
/**
 * Something that keeps the NPU from running a model, and its fix.
 */
export interface ProviderNpuProblem {
  code: string;
  fix: string;
  params: {
    [k: string]: string | undefined;
  };
  title: string;
  [k: string]: unknown | undefined;
}
/**
 * A provider's chat models; ``blocked`` marks cloud under Local only, and the
 * memory fields come with the local list.
 */
export interface ProviderModelsResponse {
  blocked?: boolean | null;
  blocked_reason?: string | null;
  capacity_error?: string | null;
  memory_gb?: number | null;
  memory_source?: string | null;
  models: ProviderModelEntry[];
  provider: "local" | "fireworks" | "amd";
  [k: string]: unknown | undefined;
}
/**
 * A provider as a paired device may see it: no key, key hint or address.
 */
export interface ProviderSummary {
  blocked: boolean;
  configured: boolean;
  id: string;
  name: string;
  network?: ProviderSummaryNetwork | null;
  privacy_notice: string;
  remote: boolean;
}
/**
 * ``reachable`` is None when the check doesn't apply to this PC.
 */
export interface ProviderSummaryNetwork {
  reachable: boolean | null;
}
export interface ProviderSummaryList {
  lemonade_error?: string | null;
  providers: ProviderSummary[];
}
export interface PullRequestRequest {
  body?: string;
  /**
   * True only after the user saw and confirmed the PR.
   */
  confirmed?: boolean;
  root: string;
  title: string;
}
export interface RemoveModelRequest {
  model_id: string;
}
export interface ResourceUsage {
  cpu?: CpuUsage | null;
  devices?: DeviceUsage[];
  /**
   * Why GPUs and NPUs can't be read, when CPU and memory still can.
   */
  devices_error?: string | null;
  interval_ms: number;
  memory?: MemoryUsage | null;
  networks?: NetworkUsage[];
  /**
   * Why network adapters can't be read, when CPU and memory still can.
   */
  networks_error?: string | null;
  /**
   * Why NPUs can't be read when they're read apart from GPUs (Linux).
   */
  npus_error?: string | null;
  /**
   * False until the first sample is in.
   */
  ready: boolean;
}
/**
 * ``confirm`` is required to reload a model on a server GAIA does not run.
 */
export interface RestartRequest {
  confirm?: boolean;
}
export interface RetryChoiceRequest {
  keep: "original" | "retry";
  message_id: number;
  session_id: string;
}
/**
 * The retried answer's numbers; zero tokens means the server did not report them.
 */
export interface RetryStats {
  input_tokens: number;
  load_seconds: number;
  model: string;
  output_tokens: number;
  retried: true;
  seconds: number;
  time_to_first_token: number;
  tokens_per_second: number;
  [k: string]: unknown | undefined;
}
export interface RetryWithRequest {
  message_id: number;
  model: string;
  session_id: string;
}
/**
 * The retried answer, held until ``/api/chat/retry-with/choose`` keeps one.
 */
export interface RetryWithResponse {
  content: string;
  message_id: number;
  model: string;
  stats: RetryStats;
  [k: string]: unknown | undefined;
}
/**
 * A chat whose answer is still being written.
 */
export interface RunningAnswer {
  session_id: string;
  title?: string | null;
}
/**
 * A document still being indexed.
 */
export interface RunningIndexJob {
  document_id: string;
  filename?: string | null;
}
/**
 * Confirmation that a schedule was deleted.
 */
export interface ScheduleDeleteResponse {
  deleted: boolean;
  [k: string]: unknown | undefined;
}
/**
 * List of scheduled tasks.
 */
export interface ScheduleListResponse {
  schedules: unknown[];
  total: number;
}
/**
 * A scheduled task.
 */
export interface ScheduleResponse {
  created_at?: string | null;
  description: string;
  description_parts: ScheduleDescription;
  error_count?: number;
  id: string;
  interval_seconds: number;
  last_error?: string | null;
  last_result?: string | null;
  last_run_at?: string | null;
  name: string;
  next_run_at?: string | null;
  prompt: string;
  run_count?: number;
  running?: boolean;
  schedule_config?: string | null;
  session_id?: string | null;
  status: string;
}
/**
 * List of schedule execution results.
 */
export interface ScheduleResultsResponse {
  results: unknown[];
  total: number;
}
/**
 * A folder or file the chat may touch, and why.
 */
export interface ScopeEntry {
  kind: "folder" | "file";
  name: string | null;
  path: string;
  removable: boolean;
  source: "added" | "gaia_documents" | "scratch" | "document" | "working_folder" | "approved" | "allowed_paths";
  [k: string]: unknown | undefined;
}
/**
 * Commands the eval's scripted user declines; empty turns it off.
 */
export interface ScriptedUserRequest {
  decline_commands?: string[];
}
/**
 * Text around a search hit, split so the UI can highlight ``match``.
 */
export interface SearchSnippet {
  after: string;
  before: string;
  match: string;
}
export interface SelectModelRequest {
  model: string;
}
/**
 * The Agent UI was told to switch to this session.
 */
export interface SessionActivateResponse {
  activated: true;
  session_id: string;
  [k: string]: unknown | undefined;
}
/**
 * Confirmation that a document was attached to a session.
 */
export interface SessionAttachResponse {
  attached: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Every file the chat changed; ``pending`` while a turn still runs.
 */
export interface SessionChanges {
  files: ChatFileChange[];
  incomplete: boolean;
  pending: boolean;
  totals: ChangeTotals;
  turns: number;
  untracked: UntrackedWork[];
  [k: string]: unknown | undefined;
}
/**
 * Work that may have changed files without GAIA keeping a copy.
 */
export interface UntrackedWork {
  detail: string;
  kind: string;
  may_write?: boolean | null;
  paths: string[];
  [k: string]: unknown | undefined;
}
/**
 * Confirmation that a session was deleted.
 */
export interface SessionDeleteResponse {
  deleted: boolean;
  [k: string]: unknown | undefined;
}
/**
 * Confirmation that a document was detached from a session.
 */
export interface SessionDetachResponse {
  detached: boolean;
  [k: string]: unknown | undefined;
}
/**
 * The folders this chat may touch and whether it may use the internet.
 */
export interface SessionEnvironment {
  enforced: boolean;
  folders: ScopeEntry[];
  internet: boolean;
  internet_enforced: boolean;
  removed: string[];
  session_id: string;
  [k: string]: unknown | undefined;
}
/**
 * The chat's repositories; ``reason`` says why git is unavailable.
 */
export interface SessionGit {
  available: boolean;
  reason?: string | null;
  repos: GitRepo[];
  [k: string]: unknown | undefined;
}
/**
 * List of sessions.
 */
export interface SessionListResponse {
  sessions: SessionResponse[];
  total: number;
}
/**
 * A chat session.
 */
export interface SessionResponse {
  agent_type?: string;
  created_at: string;
  device?: string;
  document_ids?: string[];
  effective_model?: string | null;
  id: string;
  inference_description?: string | null;
  inference_provider?: string | null;
  inference_provider_name?: string | null;
  inference_remote?: boolean | null;
  kind?: string;
  last_run_status?: string | null;
  mail_provider?: string | null;
  message_count?: number;
  model: string;
  pinned?: boolean;
  private?: boolean;
  system_prompt?: string | null;
  title: string;
  title_is_custom?: boolean;
  unread?: boolean;
  updated_at: string;
}
/**
 * Everything the chat produced, newest first.
 */
export interface SessionOutputs {
  items: OutputItem[];
  session_id: string;
  [k: string]: unknown | undefined;
}
/**
 * Pin or unpin a chat.
 */
export interface SessionPinRequest {
  pinned: boolean;
}
/**
 * Chats matching a search query, newest first, at most ``limit``.
 */
export interface SessionSearchResponse {
  query: string;
  results: SessionSearchResult[];
  truncated?: boolean;
}
/**
 * One chat that matches a search, with where the first hit was.
 */
export interface SessionSearchResult {
  field: "title" | "message";
  session_id: string;
  snippet: SearchSnippet;
  title: string;
}
/**
 * Current user settings.
 */
export interface SettingsResponse {
  agent_mode?: string;
  context_size?: number | null;
  custom_model?: string | null;
  dynamic_tools?: boolean;
  dynamic_tools_locked?: boolean;
  model_status?: ModelStatus | null;
  network_policy?: "cloud_allowed" | "local_ai_only";
  policy_notices?: PolicyNotice[];
  policy_warnings?: string[];
}
/**
 * Request to update user settings.
 */
export interface SettingsUpdateRequest {
  /**
   * Agent operating mode. One of: 'manual' (request/response only), 'goal_driven' (execute approved goals). Default: 'goal_driven'. 'autonomous' (observe, infer, and execute own goals) is not implemented yet and is rejected (#2005).
   */
  agent_mode?: string | null;
  /**
   * Context window size in tokens for model loading. Must be >= 32768 (the minimum required by GAIA Chat). Set to null to reset to the default (32768).
   */
  context_size?: number | null;
  /**
   * HuggingFace model ID to use instead of the default model. Example: huihui-ai/Huihui-Qwen3.5-35B-A3B-abliterated. Set to empty string or null to clear the override.
   */
  custom_model?: string | null;
  /**
   * Dynamic tool loading: send the tool list in short form and full docs only as needed, to cut first-turn TTFT without breaking the prompt cache. Applies to new chats. GAIA_DYNAMIC_TOOLS overrides this at runtime when set. Omit the field to leave the persisted value unchanged.
   */
  dynamic_tools?: boolean | null;
  /**
   * Whether GAIA may use cloud AI models. Setting local_ai_only blocks cloud models, clears saved cloud model choices, and removes cloud keys from GAIA's running Lemonade server. Omit to leave the policy unchanged.
   */
  network_policy?: ("cloud_allowed" | "local_ai_only") | null;
}
/**
 * A backend Lemonade reports for this PC.
 */
export interface SetupBackend {
  backend: string;
  default: boolean;
  recipe: string;
  state: string | null;
  version: string | null;
  [k: string]: unknown | undefined;
}
/**
 * Whether anything was stopped.
 */
export interface SetupCancelResponse {
  cancelled: boolean;
  [k: string]: unknown | undefined;
}
/**
 * One model setup can pull or use. Every ``*_gb`` is decimal GB.
 */
export interface SetupCatalogEntry {
  capacity_gb: number | null;
  chat_choice: boolean;
  downloaded: boolean | null;
  feature_tier: "compact" | "full";
  fit_code: ("memory" | "npu" | "backend") | null;
  fit_reason: string | null;
  fits: boolean | null;
  full_size_gb: number | null;
  gaia_model: boolean;
  id: string;
  if_skipped: string | null;
  in_lemonade: boolean;
  name: string;
  need_gb: number | null;
  partial: boolean;
  partial_gb: number;
  planned: boolean;
  purpose: string;
  reads_images: boolean;
  recipe: string | null;
  recommended: boolean;
  roles: ("chat" | "vision" | "embedding" | "speech" | "voice" | "image_creation")[];
  selected: boolean;
  size_gb: number | null;
  skipped: boolean;
  source: string | null;
  support: ("verified" | "untested") | null;
  [k: string]: unknown | undefined;
}
/**
 * The chat model setup starts from.
 */
export interface SetupChatModel {
  downloaded: boolean | null;
  id: string;
  size_gb: number | null;
  user_set: boolean;
  [k: string]: unknown | undefined;
}
/**
 * ``gaia init --check --json`` plus the step lists the screen shows.
 */
export interface SetupCheckResponse {
  agent_missing: boolean;
  cloud_steps: SetupStepView[];
  initialized: boolean;
  model_list_unread: boolean;
  models: SetupModelLoad[];
  no_agent_build: boolean;
  ready: boolean;
  reasons: string[];
  stage: string | null;
  steps: SetupStepView[];
  warnings: string[];
  [k: string]: unknown | undefined;
}
/**
 * One step of setup as the user reads it; ``status`` only while a run is on.
 */
export interface SetupStepView {
  detail: string;
  key: "server" | "models" | "finish";
  label: string;
  status?: ("pending" | "active" | "done" | "failed" | "cancelled") | null;
  [k: string]: unknown | undefined;
}
/**
 * One model the load check tried.
 */
export interface SetupModelLoad {
  downloaded: boolean | null;
  error: string | null;
  id: string;
  kind: string | null;
  loaded: boolean;
  role: string;
  size_gb: number | null;
  [k: string]: unknown | undefined;
}
/**
 * Something setup fetches besides the models.
 */
export interface SetupComponent {
  bundled?: boolean | null;
  id: "server" | "backend" | "agent";
  installed: boolean | null;
  size_gb: number | null;
  source: string;
  version: string | null;
  [k: string]: unknown | undefined;
}
/**
 * A processor Lemonade found.
 */
export interface SetupDevice {
  available: boolean;
  cores: number | null;
  family: string | null;
  integrated: boolean | null;
  kind: string;
  name: string;
  threads: number | null;
  [k: string]: unknown | undefined;
}
/**
 * The drive models download to.
 */
export interface SetupDisk {
  free_gb: number | null;
  path: string | null;
  source: ("lemonade" | "local") | null;
  [k: string]: unknown | undefined;
}
/**
 * Which GAIA is running.
 */
export interface SetupGaiaBuild {
  commit: string | null;
  kind: "release" | "release_candidate" | "local" | "source";
  rc: string | null;
  version: string;
  [k: string]: unknown | undefined;
}
/**
 * The model server setup will use.
 */
export interface SetupLemonade {
  mode: "gaia" | "external";
  reachable: boolean;
  url: string | null;
  [k: string]: unknown | undefined;
}
/**
 * Whether a job needs a model that isn't on this PC; ``off`` says why none is offered.
 */
export interface SetupModelNeedResponse {
  disk_free_gb: number | null;
  job: "chat" | "vision" | "embedding" | "speech" | "voice" | "image_creation";
  model: SetupNeedModel | null;
  needed: boolean;
  off?: "npu" | null;
  [k: string]: unknown | undefined;
}
/**
 * The model a job is missing.
 */
export interface SetupNeedModel {
  id: string;
  name: string;
  size_gb: number | null;
  source: string | null;
  [k: string]: unknown | undefined;
}
/**
 * One model's download. Sizes in bytes; speed in bytes per second.
 */
export interface SetupModelProgress {
  bytes_done: number;
  bytes_total: number;
  can_retry: boolean;
  downloaded_before_gb: number | null;
  error: string | null;
  error_kind: string | null;
  eta_s: number | null;
  id: string;
  log: string[];
  retrying_until: number | null;
  roles: ("chat" | "vision" | "embedding" | "speech" | "voice" | "image_creation")[];
  speed_bps: number;
  state: "queued" | "downloading" | "verifying" | "ready" | "failed" | "skipped" | "cancelled" | "unchecked";
  [k: string]: unknown | undefined;
}
/**
 * One thing between this PC and running a model on its NPU.
 */
export interface SetupNpuProblem {
  code: "driver" | "device" | "firmware" | "access" | "memlock" | "runtime";
  fix: string;
  params: {
    [k: string]: string | undefined;
  };
  title: string;
  [k: string]: unknown | undefined;
}
/**
 * Whether this PC has an NPU and what stops a model running on it.
 */
export interface SetupNpuStatus {
  present: boolean;
  problems: SetupNpuProblem[];
  ready: boolean;
  [k: string]: unknown | undefined;
}
/**
 * One default chat model and whether this PC can run it.
 */
export interface SetupOption {
  fits: boolean | null;
  id: string;
  reason: string | null;
  size_gb: number | null;
  [k: string]: unknown | undefined;
}
/**
 * Bytes-weighted progress across the models being set up.
 */
export interface SetupOverall {
  bytes_done: number;
  bytes_total: number;
  eta_s: number | null;
  failed: number;
  percent: number | null;
  speed_bps: number;
  [k: string]: unknown | undefined;
}
/**
 * A catalog entry setup will download, with its role in the plan.
 */
export interface SetupPlanModel {
  capacity_gb: number | null;
  chat_choice: boolean;
  downloaded: boolean | null;
  feature_tier: "compact" | "full";
  fit_code: ("memory" | "npu" | "backend") | null;
  fit_reason: string | null;
  fits: boolean | null;
  full_size_gb: number | null;
  gaia_model: boolean;
  id: string;
  if_skipped: string | null;
  in_lemonade: boolean;
  name: string;
  need_gb: number | null;
  partial: boolean;
  partial_gb: number;
  planned: boolean;
  purpose: string;
  reads_images: boolean;
  recipe: string | null;
  recommended: boolean;
  role: "chat" | "embedding";
  roles: ("chat" | "vision" | "embedding" | "speech" | "voice" | "image_creation")[];
  selected: boolean;
  size_gb: number | null;
  skipped: boolean;
  source: string | null;
  support: ("verified" | "untested") | null;
  [k: string]: unknown | undefined;
}
/**
 * What setup will use and download, before it starts.
 */
export interface SetupPlanResponse {
  backends: SetupBackend[];
  capacity_gb: number | null;
  catalog: SetupCatalogEntry[];
  chat_model: SetupChatModel;
  components: SetupComponent[];
  decided_after_server: boolean;
  disk: SetupDisk;
  disk_warning: string | null;
  download_gb: number | null;
  lemonade: SetupLemonade;
  memory_gb: number | null;
  memory_source: string | null;
  models: SetupPlanModel[];
  npu: SetupNpuStatus;
  options: SetupOption[];
  profile: string;
  recommended_model: string | null;
  skipped_models: string[];
  system: SetupSystem;
  vision_model: string | null;
  [k: string]: unknown | undefined;
}
/**
 * What setup found across GAIA, the model server and this PC.
 */
export interface SetupSystem {
  agent_version: string | null;
  devices: SetupDevice[];
  gaia_build: SetupGaiaBuild;
  gaia_home: string;
  gaia_version: string;
  lemonade_version: string | null;
  os: string | null;
  physical_memory: string | null;
  processor: string | null;
  [k: string]: unknown | undefined;
}
/**
 * A failed setup with a known fix: the user's model server.
 */
export interface SetupProblemView {
  env_url: string | null;
  kind: "server_unreachable" | "server_too_old";
  message: string;
  start: string | null;
  use_gaia: boolean;
  [k: string]: unknown | undefined;
}
/**
 * The new plan after a model is removed, and the space it freed.
 */
export interface SetupRemoveModelResponse {
  backends: SetupBackend[];
  capacity_gb: number | null;
  catalog: SetupCatalogEntry[];
  chat_model: SetupChatModel;
  components: SetupComponent[];
  decided_after_server: boolean;
  disk: SetupDisk;
  disk_warning: string | null;
  download_gb: number | null;
  freed_gb: number;
  lemonade: SetupLemonade;
  memory_gb: number | null;
  memory_source: string | null;
  models: SetupPlanModel[];
  npu: SetupNpuStatus;
  options: SetupOption[];
  profile: string;
  recommended_model: string | null;
  skipped_models: string[];
  system: SetupSystem;
  vision_model: string | null;
  [k: string]: unknown | undefined;
}
/**
 * The troubleshooting bundle just written.
 */
export interface SetupReportResponse {
  name: string;
  path: string;
  size_bytes: number;
  [k: string]: unknown | undefined;
}
/**
 * ``skip_chat_model`` when the chat runs on a cloud provider;
 * ``gaia_lemonade`` to set up GAIA's own server instead of the user's;
 * ``acknowledge_low_disk`` to download past a too-small model drive;
 * ``keep_trying`` to wait up to two minutes out a dropped connection;
 * ``extra_models`` to pull models beside the plan's (the user ticked them);
 * ``server_only`` to start the model server and stop, so the models can be
 * chosen against it before anything downloads;
 * ``upgrade_embedded`` to replace an older embedded server artifact without
 * downloading the existing model cache again.
 */
export interface SetupRunRequest {
  acknowledge_low_disk?: boolean;
  extra_models?: string[];
  gaia_lemonade?: boolean;
  keep_trying?: boolean;
  server_only?: boolean;
  skip_chat_model?: boolean;
  upgrade_embedded?: boolean;
}
/**
 * The running (or last) setup. ``idle`` carries only ``state`` and, after a
 * single model download, ``models``, ``overall`` and ``logs``.
 */
export interface SetupRunStatus {
  chat_model?: string | null;
  command?: string | null;
  component_kinds?: {
    [k: string]: string | undefined;
  } | null;
  components?: {
    [k: string]: string | undefined;
  } | null;
  error?: string | null;
  gaia_lemonade?: boolean | null;
  log_tail?: string[] | null;
  logs?: {
    [k: string]: string[] | undefined;
  } | null;
  model_failed?: boolean | null;
  models?: SetupModelProgress[] | null;
  overall?: SetupOverall | null;
  percent?: number | null;
  problem?: SetupProblemView | null;
  server_only?: boolean | null;
  size?: string | null;
  skip_chat_model?: boolean | null;
  state: "idle" | "running" | "verifying" | "ready" | "failed" | "cancelled";
  steps?: SetupStepView[] | null;
  tests?: SetupTestRow[] | null;
  text?: string | null;
  upgrade_embedded?: boolean | null;
  [k: string]: unknown | undefined;
}
/**
 * One row of the Test step; the fields come from ``gaia init``'s events.
 */
export interface SetupTestRow {
  error: string | null;
  job: string | null;
  kind: string | null;
  seconds: number | null;
  state: string | null;
  subject: string | null;
  [k: string]: unknown | undefined;
}
/**
 * A skills library offered as a one-click add.
 */
export interface SkillFeaturedLibrary {
  description: string;
  id: string;
  name: string;
  url: string;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``GET /api/skills/featured``.
 */
export interface SkillFeaturedResponse {
  libraries: SkillFeaturedLibrary[];
  [k: string]: unknown | undefined;
}
export interface SkillLinkAddRequest {
  allow_code?: boolean;
  force?: boolean;
  names?: string[] | null;
  pin: string;
  url: string;
}
/**
 * Result of ``POST /api/skills/link/add``; ``failed`` maps name to reason.
 */
export interface SkillLinkAddResponse {
  added: SkillLinkAdded[];
  failed: {
    [k: string]: string | undefined;
  };
  [k: string]: unknown | undefined;
}
/**
 * One skill the add wrote to ``~/.gaia/skills``.
 */
export interface SkillLinkAdded {
  code_trusted: boolean;
  findings: string[];
  has_code: boolean;
  name: string;
  path: string;
  updated: boolean;
  verdict: string;
  [k: string]: unknown | undefined;
}
/**
 * One skill found behind a pasted link, and what its audit said.
 */
export interface SkillLinkEntry {
  description: string;
  error: string | null;
  findings: string[];
  has_code: boolean;
  installed: boolean;
  name: string;
  path: string;
  permissions: SkillLinkPermission[];
  permissions_unreadable: boolean;
  update: boolean;
  verdict: string;
  [k: string]: unknown | undefined;
}
/**
 * One permission a linked skill declares, raw grant plus a sentence.
 */
export interface SkillLinkPermission {
  grant: string;
  text: string;
  [k: string]: unknown | undefined;
}
/**
 * Result of ``POST /api/skills/link/preview``; ``pin`` goes back on add.
 */
export interface SkillLinkPreviewResponse {
  link: string;
  pin: string;
  skills: SkillLinkEntry[];
  source: string;
  warning: string;
  [k: string]: unknown | undefined;
}
export interface SkillLinkRequest {
  url: string;
}
/**
 * One installed skill in the Skills settings list.
 */
export interface SkillListEntry {
  always_on?: boolean | null;
  description: string;
  name: string;
  path: string;
  source?: ("agent-bundled" | "user" | "claude-project" | "claude-import") | null;
  tier: "verified" | "community" | "experimental";
  tools: string[];
  version: string | null;
  [k: string]: unknown | undefined;
}
/**
 * A skill's saved preferences, keyed as its preferences schema names them.
 */
export interface SkillPreferences {
  preferences: {
    [k: string]: unknown | undefined;
  };
  skill: string;
  [k: string]: unknown | undefined;
}
/**
 * Every skill with saved preferences.
 */
export interface SkillPreferencesList {
  skills: SkillPreferences[];
  [k: string]: unknown | undefined;
}
/**
 * ``reset`` is false when the skill had nothing saved.
 */
export interface SkillPreferencesReset {
  reset: boolean;
  skill: string;
  [k: string]: unknown | undefined;
}
export interface SkillRemoved {
  removed: string;
  [k: string]: unknown | undefined;
}
/**
 * Installed skills, and any folder that failed to parse (path to message).
 */
export interface SkillsListResponse {
  invalid: {
    [k: string]: string | undefined;
  };
  skills: SkillListEntry[];
  [k: string]: unknown | undefined;
}
export interface SkipModelRequest {
  model_id: string;
  skipped?: boolean;
}
export interface SpeakRequest {
  speed?: number | null;
  text: string;
  voice?: string | null;
}
/**
 * Whether the voice model is on this PC, so a first read can say it downloads.
 */
export interface SpeechModel {
  downloaded: boolean;
  size_gb?: number | null;
}
export interface SpeechSettings {
  auto: boolean;
  max_speed?: number;
  min_speed?: number;
  speed: number;
  voice: string;
  voices: Voice[];
}
export interface Voice {
  accent: string;
  gender: string;
  id: string;
  name: string;
  quality: string;
}
export interface SpeechSettingsUpdate {
  auto?: boolean | null;
  speed?: number | null;
  voice?: string | null;
}
/**
 * One model's share of this month's cloud spending.
 */
export interface SpendingModelRow {
  model: string;
  provider: string;
  turns: number;
  unpriced_turns: number;
  unreported_turns: number;
  usd: number;
  [k: string]: unknown | undefined;
}
/**
 * This month's cloud spending, the limit, and where it stands.
 */
export interface SpendingReport {
  by_model: SpendingModelRow[];
  limit_usd: number | null;
  month: string;
  price_source: string;
  state: "no_limit" | "allowed_over" | "reached" | "near" | "ok";
  total_usd: number;
  turns: number;
  unpriced_turns: number;
  unreported_turns: number;
  [k: string]: unknown | undefined;
}
/**
 * A task a new chat offers.
 */
export interface StartCardView {
  needs_input: boolean;
  note: string;
  prompt: string;
  skill: string;
  title: string;
  [k: string]: unknown | undefined;
}
/**
 * A new chat's task cards and working folder; ``skills_error`` says why there are no cards.
 */
export interface StartScreenResponse {
  cards: StartCardView[];
  skills_error: string | null;
  working_folder: StartWorkingFolder;
  [k: string]: unknown | undefined;
}
/**
 * The folder a new chat's agent works in.
 */
export interface StartWorkingFolder {
  choosable: boolean;
  gaia_folder: boolean;
  name: string;
  path: string;
  [k: string]: unknown | undefined;
}
/**
 * System readiness status.
 */
export interface SystemStatus {
  active_profile?: string;
  config_error?: string | null;
  context_size_sufficient?: boolean;
  data_dir?: string | null;
  default_model_is_cloud?: boolean;
  default_model_name?: string | null;
  default_model_size_gb?: number | null;
  detected_devices?: string[];
  disk_space_gb?: number;
  download_progress?: DownloadProgress | null;
  embedding_model_loaded?: boolean;
  expected_model_loaded?: boolean;
  gpu_name?: string | null;
  gpu_vram_gb?: number | null;
  init_state?: string;
  init_tasks?: InitTaskInfo[];
  initialized?: boolean;
  lemonade_error?: string | null;
  lemonade_probe?: ("ok" | "down" | "timed_out" | "error") | null;
  lemonade_running?: boolean;
  lemonade_url?: string | null;
  lemonade_version?: string | null;
  memory_available_gb?: number | null;
  model_context_size?: number | null;
  model_device?: string | null;
  model_downloaded?: boolean | null;
  model_labels?: string[] | null;
  model_loaded?: string | null;
  model_size_gb?: number | null;
  probe_warnings?: string[];
  processor_name?: string | null;
  setup_error?: string | null;
  start_command?: string | null;
  start_instruction?: string | null;
  time_to_first_token?: number | null;
  tokens_per_second?: number | null;
  version?: string;
  warm_up?: WarmUpStatus | null;
}
/**
 * Whether a new chat's first turn will find its prompt already read.
 */
export interface WarmUpStatus {
  cached_tokens?: number | null;
  device?: string | null;
  elapsed_seconds?: number | null;
  error?: string | null;
  model?: string | null;
  prompt_tokens?: number | null;
  state: string;
}
/**
 * ``mode`` "gaia" or "external"; the URL and key only for "external".
 */
export interface TargetRequest {
  api_key?: string | null;
  base_url?: string | null;
  mode: string;
}
export interface TaskCreate {
  description: string;
  order_index?: number;
}
export interface TaskStatusUpdate {
  result?: string | null;
  status: string;
}
/**
 * Request body for the tool confirmation endpoint.
 *
 * ``always`` answers "allow" and grants this exact invocation for the rest of
 * the chat (see ``gaia.agents.base.tool_grants``). ``confirm_id`` echoes the
 * prompt being answered, so a late click cannot approve a newer prompt.
 */
export interface ToolConfirmRequest {
  always?: boolean;
  approved: boolean;
  confirm_id?: string | null;
  session_id: string;
}
/**
 * The device behind the latest authenticated remote request.
 */
export interface TunnelDevice {
  lastSeen: string;
  name: string;
  [k: string]: unknown | undefined;
}
/**
 * The tunnel's state.
 */
export interface TunnelStatus {
  active: boolean;
  device: TunnelDevice | null;
  error: string | null;
  installCommands: string[];
  publicIp: string | null;
  reason: "ngrok_not_installed" | null;
  startedAt: string | null;
  token: string | null;
  url: string | null;
  [k: string]: unknown | undefined;
}
export interface TunnelStopped {
  active: false;
  [k: string]: unknown | undefined;
}
/**
 * One turn's file changes.
 */
export interface TurnChanges {
  files: TurnFileChange[];
  finished_at: string | null;
  incomplete: boolean;
  message_id: number | null;
  restored_at: string | null;
  session_id: string;
  started_at: string;
  turn_id: string;
  untracked: UntrackedWork[];
  [k: string]: unknown | undefined;
}
export interface TurnFileChange {
  change: "edited" | "created" | "deleted" | "pending";
  path: string;
  tool: string | null;
  [k: string]: unknown | undefined;
}
/**
 * Every turn in the session that changed files, oldest first.
 */
export interface TurnChangesList {
  turns: TurnChanges[];
  [k: string]: unknown | undefined;
}
/**
 * What the turn did to each file, as unified diffs.
 */
export interface TurnDiff {
  files: FileDiff[];
  turn_id: string;
  [k: string]: unknown | undefined;
}
/**
 * What undoing every turn of the chat put back, removed, and could not account for.
 */
export interface UndoAllResult {
  incomplete: boolean;
  kept_folders: string[];
  removed: string[];
  restored: string[];
  turn_ids: string[];
  untracked: UntrackedWork[];
  [k: string]: unknown | undefined;
}
export interface UndoRequest {
  /**
   * Undo even files changed after the turn, losing those changes.
   */
  force?: boolean;
}
/**
 * What undoing the turn put back, removed, and could not account for.
 */
export interface UndoResult {
  incomplete: boolean;
  kept_folders: string[];
  removed: string[];
  restored: string[];
  turn_id: string;
  untracked: UntrackedWork[];
  [k: string]: unknown | undefined;
}
export interface UndoSettings {
  max_mb: number;
  retention_days: number;
}
/**
 * The undo history limits and how much of it is used.
 */
export interface UndoSettingsResponse {
  max_mb: number;
  path: string;
  retention_days: number;
  turns: number;
  used_bytes: number;
  [k: string]: unknown | undefined;
}
export interface UnloadModelRequest {
  model_name: string;
}
/**
 * The model server dropped its models from memory.
 */
export interface UnloadModelResponse {
  status: string;
}
/**
 * Request to update a scheduled task.
 */
export interface UpdateScheduleRequest {
  /**
   * New status: 'paused', 'active', or 'cancelled'
   */
  status?: string | null;
}
/**
 * Request to update a session.
 */
export interface UpdateSessionRequest {
  agent_type?: string | null;
  device?: string | null;
  document_ids?: string[] | null;
  mail_provider?: string | null;
  private?: boolean | null;
  system_prompt?: string | null;
  title?: string | null;
  title_is_custom?: boolean | null;
}
/**
 * Request body for answering a mid-run ``needs_input`` question.
 */
export interface UserInputRequest {
  request_id: string;
  session_id: string;
  value: string;
}
/**
 * The answer reached the waiting question.
 */
export interface UserInputResponse {
  request_id: string;
  status: "ok";
  [k: string]: unknown | undefined;
}
/**
 * A model that reads images, or null to go back to GAIA's choice.
 */
export interface VisionModelRequest {
  model_id?: string | null;
}
/**
 * Work that quitting or unloading the model would cut short.
 */
export interface WorkInProgress {
  answers: RunningAnswer[];
  indexing: RunningIndexJob[];
  setup?: boolean;
}
export interface Gaia_Ui_Routers_Setup__DownloadModelRequest {
  acknowledge_low_disk?: boolean;
  keep_trying?: boolean;
  model_id: string;
}
export interface Gaia_Ui_Routers_System__DownloadModelRequest {
  force?: boolean;
  load_after?: boolean;
  model_name: string;
}
