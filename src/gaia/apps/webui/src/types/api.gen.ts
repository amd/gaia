// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// AUTO-GENERATED from the Agent UI backend's pydantic models — do not edit.
// Regenerate from the repo root: python -m gaia.ui.export_openapi && (cd src/gaia/apps/webui && npm run gen:api-types)
// Field names come straight from the wire; response fields with a backend
// default are optional here even though the server always sends them.

export interface ApiSchemas {
  ActivationRequest: ActivationRequest;
  AdminClearRequest: AdminClearRequest;
  AdminSeedItem: AdminSeedItem;
  AdminSeedRequest: AdminSeedRequest;
  AgentInfo: AgentInfo;
  AgentListResponse: AgentListResponse;
  AgentStepResponse: AgentStepResponse;
  AttachDocumentRequest: AttachDocumentRequest;
  AuthorizeRequest: AuthorizeRequest;
  Body_import_agents_api_agents_import_post: BodyImportAgentsApiAgentsImportPost;
  Body_upload_document_blob_api_documents_upload_post: BodyUploadDocumentBlobApiDocumentsUploadPost;
  Body_upload_file_api_files_upload_post: BodyUploadFileApiFilesUploadPost;
  BrowseResponse: BrowseResponse;
  CancelStreamRequest: CancelStreamRequest;
  ChatRequest: ChatRequest;
  CommandOutputResponse: CommandOutputResponse;
  CompleteOnboardingRequest: CompleteOnboardingRequest;
  ConfigRequest: ConfigRequest;
  ConfigureRequest: ConfigureRequest;
  CreateScheduleRequest: CreateScheduleRequest;
  CreateSessionRequest: CreateSessionRequest;
  DiscoveryCommit: DiscoveryCommit;
  DiscoveryCommitItem: DiscoveryCommitItem;
  DiskAgentInfo: DiskAgentInfo;
  DiskAgentListResponse: DiskAgentListResponse;
  DocumentListResponse: DocumentListResponse;
  DocumentResponse: DocumentResponse;
  DocumentUploadRequest: DocumentUploadRequest;
  DownloadModelRequest: DownloadModelRequest;
  DownloadProgress: DownloadProgress;
  FileEntry: FileEntry;
  FileListResponse: FileListResponse;
  FilePreviewResponse: FilePreviewResponse;
  FileSearchResponse: FileSearchResponse;
  FileSearchResult: FileSearchResult;
  FileUploadResponse: FileUploadResponse;
  ForwardConnectionRequest: ForwardConnectionRequest;
  GoalCreate: GoalCreate;
  GoalStatusUpdate: GoalStatusUpdate;
  GrantRequest: GrantRequest;
  HTTPValidationError: HTTPValidationError;
  IndexFolderRequest: IndexFolderRequest;
  IndexFolderResponse: IndexFolderResponse;
  InferenceCommit: InferenceCommit;
  InferenceCommitItem: InferenceCommitItem;
  InferenceStatsResponse: InferenceStatsResponse;
  InitTaskInfo: InitTaskInfo;
  InstallRequest: InstallRequest;
  KnowledgeCreate: KnowledgeCreate;
  KnowledgeUpdate: KnowledgeUpdate;
  LoadModelRequest: LoadModelRequest;
  MemorySettingsBody: MemorySettingsBody;
  MessageListResponse: MessageListResponse;
  MessageResponse: MessageResponse;
  ModelStatus: ModelStatus;
  OnboardingStatus: OnboardingStatus;
  OpenFileRequest: OpenFileRequest;
  ParseScheduleRequest: ParseScheduleRequest;
  ParseScheduleResponse: ParseScheduleResponse;
  PreflightReport: PreflightReport;
  QuickLink: QuickLink;
  ScheduleListResponse: ScheduleListResponse;
  ScheduleResponse: ScheduleResponse;
  ScheduleResultsResponse: ScheduleResultsResponse;
  SessionListResponse: SessionListResponse;
  SessionResponse: SessionResponse;
  SettingsResponse: SettingsResponse;
  SettingsUpdateRequest: SettingsUpdateRequest;
  SetupRequest: SetupRequest;
  SourceInfo: SourceInfo;
  StartAgentServerRequest: StartAgentServerRequest;
  SystemStatus: SystemStatus;
  TaskCreate: TaskCreate;
  TaskListResponse: TaskListResponse;
  TaskResponse: TaskResponse;
  TaskStatusUpdate: TaskStatusUpdate;
  ToolConfirmRequest: ToolConfirmRequest;
  UpdateScheduleRequest: UpdateScheduleRequest;
  UpdateSessionRequest: UpdateSessionRequest;
  UserInputRequest: UserInputRequest;
  ValidationError: ValidationError;
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
 * A single step in the agent's execution (persisted).
 */
export interface AgentStepResponse {
  active?: boolean;
  commandOutput?: CommandOutputResponse | null;
  data?: unknown;
  decision?: string | null;
  detail?: string | null;
  fileList?: FileListResponse | null;
  id: number;
  label: string;
  latencyMs?: number | null;
  mcpServer?: string | null;
  planSteps?: string[] | null;
  policyVersion?: string | null;
  reason?: string | null;
  receiptId?: string | null;
  render?: string | null;
  result?: string | null;
  ruleIds?: string[] | null;
  success?: boolean | null;
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
 * Request to attach a document to a session.
 */
export interface AttachDocumentRequest {
  document_id: string;
}
export interface AuthorizeRequest {
  grant_agents?: string[];
  scopes?: string[];
}
export interface BodyImportAgentsApiAgentsImportPost {
  bundle: string;
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
export interface CancelStreamRequest {
  session_id: string;
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
export interface CompleteOnboardingRequest {
  completed_at?: string | null;
  skipped?: boolean;
}
/**
 * Body for ``POST /api/agents/{id}/config``.
 */
export interface ConfigRequest {
  config: {
    [k: string]: unknown | undefined;
  };
  replace?: boolean;
}
export interface ConfigureRequest {
  config?: {
    [k: string]: unknown | undefined;
  };
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
}
/**
 * Request to create a new chat session.
 */
export interface CreateSessionRequest {
  agent_type?: string | null;
  device?: string | null;
  document_ids?: string[];
  mail_provider?: string | null;
  model?: string | null;
  private?: boolean;
  system_prompt?: string | null;
  title?: string | null;
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
 * Information about an agent present under ~/.gaia/agents.
 */
export interface DiskAgentInfo {
  id: string;
  name: string;
  registered: boolean;
  registered_agent_id?: string | null;
  source?: string | null;
}
/**
 * List of custom agents found on disk.
 */
export interface DiskAgentListResponse {
  agents: DiskAgentInfo[];
  total: number;
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
 * Request to index a document by path.
 */
export interface DocumentUploadRequest {
  filepath: string;
}
export interface DownloadModelRequest {
  force?: boolean;
  model_name: string;
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
export interface GoalCreate {
  description: string;
  mode_required?: string;
  priority?: string;
  title: string;
}
export interface GoalStatusUpdate {
  progress_notes?: string | null;
  status: string;
}
export interface GrantRequest {
  scopes?: string[];
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
  output_tokens?: number;
  time_to_first_token?: number;
  tokens_per_second?: number;
}
/**
 * Summary of a boot-time initialization task (embedded in SystemStatus).
 */
export interface InitTaskInfo {
  name: string;
  status: string;
}
/**
 * Body for ``POST /api/agents/install``.
 */
export interface InstallRequest {
  id: string;
  trust_native?: boolean;
  version?: string | null;
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
export interface LoadModelRequest {
  ctx_size?: number | null;
  model_name: string;
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
  rag_sources?: SourceInfo[] | null;
  role: string;
  session_id: string;
  stats?: InferenceStatsResponse | null;
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
 * Status of a custom model on the Lemonade server.
 */
export interface ModelStatus {
  downloaded?: boolean;
  found?: boolean;
  loaded?: boolean;
}
export interface OnboardingStatus {
  completed_at?: string | null;
  initialized?: boolean;
  skipped?: boolean;
}
/**
 * Request to open a file or folder in the system file explorer.
 */
export interface OpenFileRequest {
  path: string;
  reveal?: boolean;
}
/**
 * Request to parse a natural language schedule description.
 */
export interface ParseScheduleRequest {
  /**
   * Natural language schedule description
   */
  input: string;
}
/**
 * Parsed schedule configuration.
 */
export interface ParseScheduleResponse {
  days_of_week?: number[] | null;
  description: string;
  end_hour?: number | null;
  interval_seconds: number;
  next_run_at?: string | null;
  start_hour?: number | null;
  time_of_day?: string | null;
  valid: boolean;
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
  error_count?: number;
  id: string;
  interval_seconds: number;
  last_result?: string | null;
  last_run_at?: string | null;
  name: string;
  next_run_at?: string | null;
  prompt: string;
  run_count?: number;
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
  id: string;
  mail_provider?: string | null;
  message_count?: number;
  model: string;
  private?: boolean;
  system_prompt?: string | null;
  title: string;
  title_is_custom?: boolean;
  updated_at: string;
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
   * Beta: enable the semantic dynamic tool loader (#1798), which trims each turn's tool prompt to a matched subset to cut first-turn TTFT. Default off. GAIA_DYNAMIC_TOOLS overrides this at runtime when set. Omit the field to leave the persisted value unchanged.
   */
  dynamic_tools?: boolean | null;
}
/**
 * Body for ``POST /api/agents/setup`` (progressive multi-agent install).
 */
export interface SetupRequest {
  ids: string[];
  max_parallel?: number;
  resume?: boolean;
}
export interface StartAgentServerRequest {
  backend_url?: string;
  port?: number;
}
/**
 * System readiness status.
 */
export interface SystemStatus {
  active_profile?: string;
  context_size_sufficient?: boolean;
  default_model_name?: string;
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
  lemonade_running?: boolean;
  lemonade_url?: string;
  lemonade_version?: string | null;
  memory_available_gb?: number | null;
  model_context_size?: number | null;
  model_device?: string | null;
  model_downloaded?: boolean | null;
  model_labels?: string[] | null;
  model_loaded?: string | null;
  model_size_gb?: number | null;
  processor_name?: string | null;
  start_command?: string | null;
  start_instruction?: string | null;
  time_to_first_token?: number | null;
  tokens_per_second?: number | null;
  version?: string;
}
export interface TaskCreate {
  description: string;
  order_index?: number;
}
/**
 * List of background tasks.
 */
export interface TaskListResponse {
  tasks: TaskResponse[];
}
/**
 * A single background task visible to the frontend.
 */
export interface TaskResponse {
  error?: string | null;
  id: string;
  name: string;
  status: string;
}
export interface TaskStatusUpdate {
  result?: string | null;
  status: string;
}
/**
 * Request body for the tool confirmation endpoint.
 */
export interface ToolConfirmRequest {
  approved: boolean;
  session_id: string;
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
