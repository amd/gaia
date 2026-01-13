# go.ps1
#
# Generates meeting transcript test data and evaluates several small models
#

# Look at command line flags
param (
    [switch]$generate = $false,			# Generate synthetic meeting transcripts
    [switch]$groundTruth = $false,		# Generate ground truth summarizations
	[switch]$experiment = $false,		# Run experiments on models
	[switch]$eval = $false,				# Evaluate results
	[switch]$report = $false,			# Generate human readable report
	[switch]$visualize = $false,		# Launch interactive visualizer
	[switch]$all = $false,				# Run all of the above
	[switch]$test = $false				# Test mode runs through with a single meeting type and count per type
)


###################################################################################
#
# Setup for meeting transcription summarization
#
###################################################################################

$basicSummarizationConfig = ".\src\gaia\eval\configs\basic_summarization_lfm2.json"

$meetingTypes = "standup planning client_call design_review performance_review all_hands budget_planning product_roadmap"
$countPerType = 3

if ($test) {
	$meetingTypes = "standup"
	$countPerType = 1
}

###################################################################################
#
# Execute meeting transcript model evaluation
#
###################################################################################

# Activate virtual environment (assumes .venv in project root)
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "Warning: Virtual environment not found at .\.venv" -ForegroundColor Yellow
    Write-Host "Please create one with: python -m venv .venv" -ForegroundColor Yellow
}

if ($generate -Or $all) {
	# Step 1: Generate synthetic meeting transcripts
	$cmdStr = "gaia generate --meeting-transcript -o ./output/test_data/meetings --meeting-types $meetingTypes --count-per-type $countPerType"
	Write-Host "Command: $cmdStr" -ForegroundColor cyan
	Invoke-Expression $cmdStr
}

if ($groundTruth -Or $all) {
	# Step 2: Create evaluation standards (ground truth)
	$cmdStr = "gaia groundtruth -d ./output/test_data/meetings --use-case summarization -o ./output/groundtruth"
	Write-Host "Command: $cmdStr" -ForegroundColor cyan
	Invoke-Expression $cmdStr
}

if ($experiment -Or $all) {
	# Step 3: Run experiments with multiple models
	$cmdStr = "gaia batch-experiment -c $basicSummarizationConfig -i ./output/test_data/meetings -o ./output/experiments --skip-existing"
	Write-Host "Command: $cmdStr" -ForegroundColor cyan
	Invoke-Expression $cmdStr
}

if ($eval -Or $all) {
	# Step 4: Evaluate results and calculate scores
	$cmdStr = "gaia eval -d ./output/experiments -g ./output/groundtruth/consolidated_summarization_groundtruth.json -o ./output/evaluation"
	Write-Host "Command: $cmdStr" -ForegroundColor cyan
	Invoke-Expression $cmdStr
}

if ($report -Or $all) {
	# Step 5: Generate human-readable report
	$cmdStr = "gaia report -d ./output/evaluation -o ./output/reports/evaluation_report.md"
	Write-Host "Command: $cmdStr" -ForegroundColor cyan
	Invoke-Expression $cmdStr
}

if ($visualize -Or $all) {
	# Step 6: Launch interactive visualizer for detailed analysis
	$cmdStr = "gaia visualize --experiments-dir ./output/experiments --evaluations-dir ./output/evaluation --test-data-dir ./output/test_data --groundtruth-dir ./output/groundtruth"
	Write-Host "Command: $cmdStr" -ForegroundColor cyan
	Invoke-Expression $cmdStr
}

# Deactivate virtual environment
deactivate
