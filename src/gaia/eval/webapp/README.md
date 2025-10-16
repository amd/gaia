# Gaia Evaluation Results Visualizer

A simple Node.js web application for visualizing and comparing Gaia evaluation experiment results.

## Features

- **Load Multiple Reports**: View experiment results (`.experiment.json`) and evaluation reports (`.experiment.eval.json`)
- **Side-by-Side Comparison**: Compare multiple reports simultaneously in a clean grid layout
- **Key Metrics Dashboard**: View costs, token usage, quality scores, and performance metrics
- **Quality Analysis**: Detailed breakdown of evaluation criteria and ratings
- **Responsive Design**: Works on desktop and mobile devices

## Quick Start

### Prerequisites

- Node.js (v14 or higher)
- npm

### Using the GAIA CLI (Recommended)

The easiest way to launch the visualizer is using the GAIA CLI command:

```bash
gaia visualize
```

This will automatically:
- Install npm dependencies if needed (first time only)
- Start the server on port 3000
- Open your browser to the visualizer

Options:
```bash
# Use custom port
gaia visualize --port 8080

# Don't open browser automatically
gaia visualize --no-browser

# Specify custom data directories
gaia visualize --experiments-dir ./my_experiments --evaluations-dir ./my_evaluations
```

### Manual Installation

If you prefer to run the server manually:

1. Navigate to the webapp directory:
   ```bash
   cd src/gaia/eval/webapp
   ```

2. Install dependencies (only needed once):
   ```bash
   npm install
   ```

3. Start the server:
   ```bash
   npm start
   ```

4. Open your browser and go to:
   ```
   http://localhost:3000
   ```

## Usage

### Loading Reports

1. **Select Files**: Use the file selectors to choose experiment and/or evaluation files
   - **Experiments**: Shows `.experiment.json` files from the `experiments/` folder
   - **Evaluations**: Shows `.experiment.eval.json` files from the `evaluation/` folder

2. **Add Reports**: Click "Add Report" to load selected files into the visualization

3. **Compare**: Reports will appear side-by-side in the main view for easy comparison

### Understanding the Data

#### Experiment Reports Show:
- **Total Cost**: Cost breakdown (input/output tokens)
- **Token Usage**: Total tokens consumed
- **Model Details**: LLM model, temperature, max tokens
- **Experiment Metadata**: Timestamp, parameters, errors

#### Evaluation Reports Show:
- **Quality Scores**: Overall quality metrics
- **Rating Distribution**: Excellent/Good/Fair/Poor counts
- **Detailed Analysis**: Per-criteria quality ratings
- **Sample Quality Breakdown**: Executive summary, completeness, etc.

### Controls

- **Add Report**: Load selected files from the lists
- **Compare Selected**: Scroll to view all loaded reports (shows message if <2 reports)
- **Clear All**: Remove all loaded reports
- **×** (on report cards): Remove individual reports

## File Structure

```
webapp/
├── package.json          # Node.js dependencies
├── server.js             # Express server with API endpoints
├── public/
│   ├── index.html        # Main application interface
│   ├── styles.css        # CSS styling
│   └── app.js           # Frontend JavaScript logic
└── README.md            # This file
```

## API Endpoints

The web app provides these REST endpoints:

- `GET /api/files` - List available experiment and evaluation files
- `GET /api/experiment/:filename` - Load specific experiment data
- `GET /api/evaluation/:filename` - Load specific evaluation data
- `GET /api/report/:experimentFile/:evaluationFile?` - Combined report data

## Data Sources

The app automatically discovers files from:
- `experiments/` folder: `*.experiment.json` files
- `evaluation/` folder: `*.experiment.eval.json` files

Make sure your experiment and evaluation files are in these locations relative to the webapp directory.

## Development

To modify the app:

1. **Backend**: Edit `server.js` for API changes
2. **Frontend**: Edit files in `public/` folder
3. **Styling**: Modify `public/styles.css`
4. **Logic**: Update `public/app.js`

The server automatically serves static files from the `public/` folder.

## Troubleshooting

### No Files Showing
- Ensure experiment/evaluation files exist in the correct folders
- Check the console logs for file path errors
- Verify file naming conventions (`.experiment.json`, `.experiment.eval.json`)

### Server Won't Start
- Check that port 3000 is available
- Ensure Node.js dependencies are installed (`npm install`)
- Verify you're in the correct directory (`src/gaia/eval/webapp`)

### Data Not Loading
- Check browser console for API errors
- Verify JSON file format is valid
- Ensure file permissions allow reading

## Future Enhancements

Potential improvements for future versions:
- Export comparison reports
- More detailed visualization charts
- Advanced filtering and sorting
- Drag and drop file uploads
- Real-time data updates
- Enhanced comparison views 