// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Package setupapi reads setup's model plan and GPU memory split from the GAIA
// daemon, and sends the user's choices back.
//
// The daemon serves the Agent UI's own setup handlers under /daemon/v1/setup
// and /daemon/v1/gpu-memory, so the TUI and the Agent UI read one plan and make
// one set of choices. Nothing here decides what setup downloads; it only
// carries the backend's answer. Every *_gb field is decimal GB (10^9 bytes).
package setupapi

// Plan is GET /daemon/v1/setup/plan.
type Plan struct {
	Profile            string         `json:"profile"`
	Lemonade           LemonadeInfo   `json:"lemonade"`
	ChatModel          ChatModel      `json:"chat_model"`
	RecommendedModel   *string        `json:"recommended_model"`
	DecidedAfterServer bool           `json:"decided_after_server"`
	CapacityGB         *float64       `json:"capacity_gb"`
	MemoryGB           *float64       `json:"memory_gb"`
	MemorySource       *string        `json:"memory_source"`
	Disk               Disk           `json:"disk"`
	DownloadGB         *float64       `json:"download_gb"`
	DiskWarning        *string        `json:"disk_warning"`
	SkippedModels      []string       `json:"skipped_models"`
	Catalog            []CatalogEntry `json:"catalog"`
	Models             []CatalogEntry `json:"models"`
}

// LemonadeInfo is which model server setup uses, and whether it answered.
type LemonadeInfo struct {
	Mode      string  `json:"mode"`
	URL       *string `json:"url"`
	Reachable bool    `json:"reachable"`
}

// ChatModel is the chat model setup will use.
type ChatModel struct {
	ID         string   `json:"id"`
	UserSet    bool     `json:"user_set"`
	SizeGB     *float64 `json:"size_gb"`
	Downloaded *bool    `json:"downloaded"`
}

// Disk is the drive Lemonade stores models on. FreeGB is nil when unknown.
type Disk struct {
	Path   *string  `json:"path"`
	FreeGB *float64 `json:"free_gb"`
	Source *string  `json:"source"`
}

// Roles a catalog model can have.
const (
	RoleChat      = "chat"
	RoleVision    = "vision"
	RoleEmbedding = "embedding"
)

// CatalogEntry is one model setup can pull or use.
type CatalogEntry struct {
	ID          string   `json:"id"`
	Name        string   `json:"name"`
	Roles       []string `json:"roles"`
	Purpose     string   `json:"purpose"`
	IfSkipped   *string  `json:"if_skipped"`
	Recipe      *string  `json:"recipe"`
	SizeGB      *float64 `json:"size_gb"`
	FullSizeGB  *float64 `json:"full_size_gb"`
	Partial     bool     `json:"partial"`
	PartialGB   float64  `json:"partial_gb"`
	Downloaded  *bool    `json:"downloaded"`
	InLemonade  bool     `json:"in_lemonade"`
	Recommended bool     `json:"recommended"`
	Planned     bool     `json:"planned"`
	Selected    bool     `json:"selected"`
	Skipped     bool     `json:"skipped"`
	ChatChoice  bool     `json:"chat_choice"`
	Fits        *bool    `json:"fits"`
	NeedGB      *float64 `json:"need_gb"`
	CapacityGB  *float64 `json:"capacity_gb"`
	FitReason   *string  `json:"fit_reason"`
	// Role is set on Plan.Models only: "chat" or "embedding".
	Role string `json:"role,omitempty"`
}

// HasRole reports whether the model does that job.
func (e CatalogEntry) HasRole(role string) bool {
	for _, r := range e.Roles {
		if r == role {
			return true
		}
	}
	return false
}

// IsDownloaded is false for a model not on disk and for an unknown answer.
func (e CatalogEntry) IsDownloaded() bool { return e.Downloaded != nil && *e.Downloaded }

// RunsOnNPU is true for an FLM (NPU) model.
func (e CatalogEntry) RunsOnNPU() bool { return e.Recipe != nil && *e.Recipe == "flm" }

// Ack is a 409 that asks the user to confirm before going on.
type Ack struct {
	Code    string   `json:"code"`
	Message string   `json:"message"`
	ModelID string   `json:"model_id"`
	NeedGB  *float64 `json:"need_gb"`
	// CapacityGB is the PC's memory for models (no_fit).
	CapacityGB *float64 `json:"capacity_gb"`
	// FreeGB, FreeUpGB, Path and Drop describe a disk shortfall (low_disk).
	FreeGB      *float64 `json:"free_gb"`
	FreeUpGB    *float64 `json:"free_up_gb"`
	Path        string   `json:"path"`
	Drop        []string `json:"drop"`
	Acknowledge string   `json:"acknowledge"`
}

// Confirmation codes.
const (
	AckNoFit   = "no_fit"
	AckLowDisk = "low_disk"
)

// Status is GET /daemon/v1/setup/status.
type Status struct {
	State  string          `json:"state"`
	Text   string          `json:"text"`
	Error  *string         `json:"error"`
	Models []ModelProgress `json:"models"`
	// Overall is nil until a run or download has reported.
	Overall   *Overall `json:"overall"`
	ChatModel *string  `json:"chat_model"`
	LogTail   []string `json:"log_tail"`
	Command   string   `json:"command"`
}

// Run states.
const (
	RunIdle      = "idle"
	RunRunning   = "running"
	RunVerifying = "verifying"
	RunReady     = "ready"
	RunFailed    = "failed"
	RunCancelled = "cancelled"
)

// Running reports whether a setup run is still working.
func (s Status) Running() bool { return s.State == RunRunning || s.State == RunVerifying }

// Model states.
const (
	StateQueued      = "queued"
	StateDownloading = "downloading"
	StateVerifying   = "verifying"
	StateReady       = "ready"
	StateFailed      = "failed"
	StateSkipped     = "skipped"
	StateCancelled   = "cancelled"
)

// ModelProgress is one model's download.
type ModelProgress struct {
	ID                 string   `json:"id"`
	Roles              []string `json:"roles"`
	State              string   `json:"state"`
	BytesDone          int64    `json:"bytes_done"`
	BytesTotal         int64    `json:"bytes_total"`
	SpeedBPS           int64    `json:"speed_bps"`
	ETAS               *int64   `json:"eta_s"`
	Error              *string  `json:"error"`
	ErrorKind          *string  `json:"error_kind"`
	CanRetry           bool     `json:"can_retry"`
	RetryingUntil      *int64   `json:"retrying_until"`
	DownloadedBeforeGB *float64 `json:"downloaded_before_gb"`
}

// Error kinds.
const (
	ErrNetwork  = "network"
	ErrDiskFull = "disk_full"
	ErrLemonade = "lemonade"
	ErrVerify   = "verify"
)

// Overall is the bytes-weighted total across the models being set up.
type Overall struct {
	BytesDone  int64  `json:"bytes_done"`
	BytesTotal int64  `json:"bytes_total"`
	SpeedBPS   int64  `json:"speed_bps"`
	ETAS       *int64 `json:"eta_s"`
	Percent    *int   `json:"percent"`
	Failed     int    `json:"failed"`
}

// RunRequest is POST /daemon/v1/setup/run.
type RunRequest struct {
	SkipChatModel      bool `json:"skip_chat_model"`
	AcknowledgeLowDisk bool `json:"acknowledge_low_disk"`
	KeepTrying         bool `json:"keep_trying"`
}

// GPU is GET /daemon/v1/gpu-memory.
type GPU struct {
	Applies      bool        `json:"applies"`
	ShowInSetup  bool        `json:"show_in_setup"`
	HiddenReason *string     `json:"hidden_reason"`
	OS           string      `json:"os"`
	TotalGB      float64     `json:"total_gb"`
	GPUGB        float64     `json:"gpu_gb"`
	CPUGB        float64     `json:"cpu_gb"`
	Setting      GPUSetting  `json:"setting"`
	ReserveGB    float64     `json:"reserve_gb"`
	Method       string      `json:"method"`
	MethodReason string      `json:"method_reason"`
	NeedsAdmin   bool        `json:"needs_admin"`
	NeedsRestart bool        `json:"needs_restart"`
	Presets      []GPUPreset `json:"presets"`
	Recommended  *float64    `json:"recommended_setting_gb"`
	Models       []GPUModel  `json:"models"`
	Pending      *GPUPending `json:"pending"`
	Revert       GPURevert   `json:"revert"`
	GuidedSteps  []string    `json:"guided_steps"`
	// GuidedValue is the one value the steps ask the user to enter, in the
	// units the place they enter it counts in (AMD Software's GB on Windows,
	// 4 KiB pages on Linux). Nil when the PC has nothing to give.
	GuidedValue *string `json:"guided_value"`
}

// GPU methods.
const (
	MethodApply       = "apply"
	MethodGuided      = "guided"
	MethodUnsupported = "unsupported"
)

// GPUSetting is the one knob the user changes.
type GPUSetting struct {
	Name      string  `json:"name"`
	CurrentGB float64 `json:"current_gb"`
	MinGB     float64 `json:"min_gb"`
	MaxGB     float64 `json:"max_gb"`
}

// GPUPreset is one safe value for the setting, and what it unlocks.
type GPUPreset struct {
	SettingGB float64  `json:"setting_gb"`
	GPUGB     float64  `json:"gpu_gb"`
	Unlocks   []string `json:"unlocks"`
}

// GPUModel is a model and the setting it needs.
type GPUModel struct {
	ID              string   `json:"id"`
	SizeGB          *float64 `json:"size_gb"`
	NeedGB          *float64 `json:"need_gb"`
	Default         bool     `json:"default"`
	FitsNow         bool     `json:"fits_now"`
	FitsAtSettingGB *float64 `json:"fits_at_setting_gb"`
}

// GPUPending is a change waiting for (or past) a restart.
type GPUPending struct {
	SettingGB         float64  `json:"setting_gb"`
	PreviousSettingGB *float64 `json:"previous_setting_gb"`
	Source            string   `json:"source"`
	State             string   `json:"state"`
}

// Pending states.
const (
	PendingRestart    = "restart_required"
	PendingTookEffect = "took_effect"
	PendingNotApplied = "not_applied"
)

// GPURevert is whether the last change can be undone.
type GPURevert struct {
	Available         bool     `json:"available"`
	PreviousSettingGB *float64 `json:"previous_setting_gb"`
}
