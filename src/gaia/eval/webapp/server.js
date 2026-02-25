// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

const express = require('express');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

// Rate limiting for API endpoints
const requestCounts = new Map();
const RATE_LIMIT_WINDOW = 60 * 1000; // 1 minute
const RATE_LIMIT_MAX = 100; // requests per window

function rateLimiter(req, res, next) {
    const ip = req.ip;
    const now = Date.now();
    const windowStart = now - RATE_LIMIT_WINDOW;

    if (!requestCounts.has(ip)) {
        requestCounts.set(ip, []);
    }

    const requests = requestCounts.get(ip).filter(t => t > windowStart);
    requests.push(now);
    requestCounts.set(ip, requests);

    if (requests.length > RATE_LIMIT_MAX) {
        return res.status(429).json({ error: 'Too many requests. Please try again later.' });
    }
    next();
}

app.use('/api/', rateLimiter);

/**
 * Safely read a JSON file within a base directory.
 * Returns null if the path escapes the base directory.
 */
function safeReadJson(basePath, userInput) {
    const resolvedBase = path.resolve(basePath);
    const resolvedPath = path.resolve(basePath, userInput);
    // Ensure the resolved path is within the base directory
    if (resolvedPath !== resolvedBase && !resolvedPath.startsWith(resolvedBase + path.sep)) {
        return { error: 'Invalid file path', status: 400 };
    }
    if (!fs.existsSync(resolvedPath)) {
        return { error: 'File not found', status: 404 };
    }
    return { data: JSON.parse(fs.readFileSync(resolvedPath, 'utf8')), resolvedPath };
}

// Serve static files
app.use(express.static(path.join(__dirname, 'public')));

// Parse JSON bodies
app.use(express.json());

// Base paths for data files - use environment variables or defaults
const EXPERIMENTS_PATH = process.env.EXPERIMENTS_PATH || path.join(__dirname, '../../../..', 'experiments');
const EVALUATIONS_PATH = process.env.EVALUATIONS_PATH || path.join(__dirname, '../../../..', 'evaluation');
const TEST_DATA_PATH = process.env.TEST_DATA_PATH || path.join(__dirname, '../../../..', 'test_data');
const GROUNDTRUTH_PATH = process.env.GROUNDTRUTH_PATH || path.join(__dirname, '../../../..', 'groundtruth');
const AGENT_OUTPUTS_PATH = process.env.AGENT_OUTPUTS_PATH || path.join(__dirname, '../../../..');
const SINGLE_AGENT_FILE = process.env.SINGLE_AGENT_FILE;

// API endpoint to list available files
app.get('/api/files', (req, res) => {
    try {
        const experiments = fs.existsSync(EXPERIMENTS_PATH)
            ? fs.readdirSync(EXPERIMENTS_PATH).filter(file => file.endsWith('.experiment.json'))
            : [];

        let evaluations = [];
        if (fs.existsSync(EVALUATIONS_PATH)) {
            // Get files from root
            const rootFiles = fs.readdirSync(EVALUATIONS_PATH).filter(file =>
                file.endsWith('.experiment.eval.json') ||
                file === 'consolidated_evaluations_report.json' ||
                file.endsWith('_evaluations_report.json'));
            evaluations.push(...rootFiles.map(file => ({
                name: file,
                path: path.join(EVALUATIONS_PATH, file),
                type: 'evaluation',
                directory: 'root'
            })));

            // Check for subdirectories
            const items = fs.readdirSync(EVALUATIONS_PATH, { withFileTypes: true });
            for (const item of items) {
                if (item.isDirectory()) {
                    const subDirPath = path.join(EVALUATIONS_PATH, item.name);
                    const subDirFiles = fs.readdirSync(subDirPath).filter(file =>
                        file.endsWith('.experiment.eval.json') ||
                        file === 'consolidated_evaluations_report.json' ||
                        file.endsWith('_evaluations_report.json'));
                    evaluations.push(...subDirFiles.map(file => ({
                        name: `${item.name}/${file}`,
                        path: path.join(subDirPath, file),
                        type: 'evaluation',
                        directory: item.name
                    })));
                }
            }
        }

        // Collect agent outputs
        let agentOutputs = [];
        if (SINGLE_AGENT_FILE && fs.existsSync(SINGLE_AGENT_FILE)) {
            // Single file mode
            agentOutputs.push({
                name: path.basename(SINGLE_AGENT_FILE),
                path: SINGLE_AGENT_FILE,
                type: 'agent_output',
                directory: 'single'
            });
        } else if (fs.existsSync(AGENT_OUTPUTS_PATH)) {
            // Directory mode - look for agent_output_*.json files
            const agentFiles = fs.readdirSync(AGENT_OUTPUTS_PATH).filter(file =>
                file.startsWith('agent_output_') && file.endsWith('.json'));
            agentOutputs = agentFiles.map(file => ({
                name: file,
                path: path.join(AGENT_OUTPUTS_PATH, file),
                type: 'agent_output',
                directory: 'root'
            }));
        }

        res.json({
            experiments: experiments.map(file => ({
                name: file,
                path: path.join(EXPERIMENTS_PATH, file),
                type: 'experiment'
            })),
            evaluations: evaluations,
            agentOutputs: agentOutputs,
            paths: {
                experiments: EXPERIMENTS_PATH,
                evaluations: EVALUATIONS_PATH,
                testData: TEST_DATA_PATH,
                groundtruth: GROUNDTRUTH_PATH
            }
        });
    } catch (error) {
        res.status(500).json({ error: 'Failed to list files', details: error.message });
    }
});

// API endpoint to load experiment data
app.get('/api/experiment/:filename', (req, res) => {
    try {
        const result = safeReadJson(EXPERIMENTS_PATH, req.params.filename);
        if (result.error) return res.status(result.status).json({ error: result.error });
        res.json(result.data);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load experiment', details: error.message });
    }
});

// API endpoint to load evaluation data (supports subdirectories)
app.get('/api/evaluation/*', (req, res) => {
    try {
        const result = safeReadJson(EVALUATIONS_PATH, req.params[0]);
        if (result.error) return res.status(result.status).json({ error: result.error });
        res.json(result.data);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load evaluation', details: error.message });
    }
});

// API endpoint to load agent output data
app.get('/api/agent-output/:filename(*)', (req, res) => {
    try {
        const filename = req.params.filename;
        let filePath;

        // Check if it's a single file mode or directory mode
        if (SINGLE_AGENT_FILE && fs.existsSync(SINGLE_AGENT_FILE) &&
            path.basename(SINGLE_AGENT_FILE) === filename) {
            filePath = SINGLE_AGENT_FILE;
        } else {
            const resolvedBase = path.resolve(AGENT_OUTPUTS_PATH);
            filePath = path.resolve(AGENT_OUTPUTS_PATH, filename);
            if (filePath !== resolvedBase && !filePath.startsWith(resolvedBase + path.sep)) {
                return res.status(400).json({ error: 'Invalid file path' });
            }
        }

        if (!fs.existsSync(filePath)) {
            return res.status(404).json({ error: 'Agent output file not found' });
        }

        const data = JSON.parse(fs.readFileSync(filePath, 'utf8'));

        // Process the agent output data to extract useful information
        const processedData = {
            ...data,
            metadata: {
                filename: filename,
                filepath: filePath,
                fileSize: fs.statSync(filePath).size,
                lastModified: fs.statSync(filePath).mtime,
                conversationLength: data.conversation ? data.conversation.length : 0,
                hasPerformanceStats: data.conversation ?
                    data.conversation.some(msg => msg.role === 'system' && msg.content?.type === 'stats') : false
            }
        };

        res.json(processedData);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load agent output', details: error.message });
    }
});

// API endpoint to get combined report (experiment + evaluation)
app.get('/api/report/:experimentFile/:evaluationFile?', (req, res) => {
    try {
        // Load experiment data
        const expResult = safeReadJson(EXPERIMENTS_PATH, req.params.experimentFile);
        if (expResult.error) return res.status(expResult.status).json({ error: expResult.error });

        let evaluationData = null;
        if (req.params.evaluationFile) {
            const evalResult = safeReadJson(EVALUATIONS_PATH, req.params.evaluationFile);
            if (!evalResult.error) {
                evaluationData = evalResult.data;
            }
        }

        res.json({
            experiment: expResult.data,
            evaluation: evaluationData,
            combined: true
        });
    } catch (error) {
        res.status(500).json({ error: 'Failed to load report', details: error.message });
    }
});

// API endpoint to list test data directories and files
app.get('/api/test-data', (req, res) => {
    try {
        const testData = { directories: [], files: [] };

        if (!fs.existsSync(TEST_DATA_PATH)) {
            return res.json(testData);
        }

        const entries = fs.readdirSync(TEST_DATA_PATH, { withFileTypes: true });

        // Check if TEST_DATA_PATH itself contains data files (user pointed to specific subdirectory)
        const rootDataFiles = entries
            .filter(entry => entry.isFile() && (entry.name.endsWith('.txt') || entry.name.endsWith('.pdf')))
            .map(entry => entry.name);

        if (rootDataFiles.length > 0) {
            // User pointed directly at a data directory (e.g., test_data/meetings)
            const hasMetadata = entries.some(entry =>
                entry.isFile() && entry.name.endsWith('_metadata.json')
            );

            // Use the directory name from the path
            const dirName = path.basename(TEST_DATA_PATH);

            testData.directories.push({
                name: dirName,
                path: TEST_DATA_PATH,
                files: rootDataFiles,
                hasMetadata: hasMetadata
            });
        } else {
            // User pointed at parent directory - scan for subdirectories
            for (const entry of entries) {
                if (entry.isDirectory()) {
                    const dirPath = path.join(TEST_DATA_PATH, entry.name);
                    const dirFiles = fs.readdirSync(dirPath, { withFileTypes: true });

                    const dataFiles = dirFiles
                        .filter(file => file.isFile() && (file.name.endsWith('.txt') || file.name.endsWith('.pdf')))
                        .map(file => file.name);

                    const hasMetadata = dirFiles.some(file =>
                        file.isFile() && file.name.endsWith('_metadata.json')
                    );

                    testData.directories.push({
                        name: entry.name,
                        path: dirPath,
                        files: dataFiles,
                        hasMetadata: hasMetadata
                    });
                }
            }
        }

        res.json(testData);
    } catch (error) {
        res.status(500).json({ error: 'Failed to list test data', details: error.message });
    }
});

// API endpoint to load test data file content
app.get('/api/test-data/:type/:filename', (req, res) => {
    try {
        const type = req.params.type;
        const filename = req.params.filename;

        // Validate type doesn't contain path traversal
        const resolvedTypeDir = path.resolve(TEST_DATA_PATH, type);
        if (!resolvedTypeDir.startsWith(path.resolve(TEST_DATA_PATH) + path.sep)) {
            return res.status(400).json({ error: 'Invalid type parameter' });
        }

        // Try subdirectory first, then root level
        let filePath = path.resolve(resolvedTypeDir, filename);
        if (!filePath.startsWith(resolvedTypeDir + path.sep) && filePath !== resolvedTypeDir) {
            return res.status(400).json({ error: 'Invalid file path' });
        }

        // If not found in subdirectory, try root level
        if (!fs.existsSync(filePath)) {
            const rootPath = path.resolve(TEST_DATA_PATH, filename);
            if (rootPath.startsWith(path.resolve(TEST_DATA_PATH) + path.sep) && fs.existsSync(rootPath)) {
                filePath = rootPath;
            } else {
                return res.status(404).json({ error: 'Test data file not found' });
            }
        }

        // Check if file is PDF
        if (filename.endsWith('.pdf')) {
            // For PDFs, send file info and indicate it's a binary file
            const stats = fs.statSync(filePath);
            res.json({
                filename: filename,
                type: type,
                isPdf: true,
                size: stats.size,
                message: 'PDF file - preview not available. Please open the file directly to view contents.'
            });
        } else {
            // For text files, send the content
            const content = fs.readFileSync(filePath, 'utf8');
            res.json({
                filename: filename,
                type: type,
                content: content
            });
        }
    } catch (error) {
        res.status(500).json({ error: 'Failed to load test data file', details: error.message });
    }
});

// API endpoint to load test data metadata
app.get('/api/test-data/:type/metadata', (req, res) => {
    try {
        const type = req.params.type;

        // Validate type directory
        const resolvedTypeDir = path.resolve(TEST_DATA_PATH, type);
        if (!resolvedTypeDir.startsWith(path.resolve(TEST_DATA_PATH) + path.sep)) {
            return res.status(400).json({ error: 'Invalid type parameter' });
        }

        const metadataFiles = [
            `${type}_metadata.json`,
            'metadata.json'
        ];

        let metadataPath = null;
        for (const filename of metadataFiles) {
            const potentialPath = path.resolve(resolvedTypeDir, filename);
            if (potentialPath.startsWith(resolvedTypeDir + path.sep) && fs.existsSync(potentialPath)) {
                metadataPath = potentialPath;
                break;
            }
        }

        if (!metadataPath) {
            return res.status(404).json({ error: 'Metadata file not found' });
        }

        const metadata = JSON.parse(fs.readFileSync(metadataPath, 'utf8'));
        res.json(metadata);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load metadata', details: error.message });
    }
});

// API endpoint to list groundtruth files
app.get('/api/groundtruth', (req, res) => {
    try {
        const groundtruthData = { directories: [], files: [] };

        if (!fs.existsSync(GROUNDTRUTH_PATH)) {
            return res.json(groundtruthData);
        }

        // Function to recursively find groundtruth files
        function findGroundtruthFiles(dir, relativePath = '') {
            const entries = fs.readdirSync(dir, { withFileTypes: true });

            for (const entry of entries) {
                const fullPath = path.join(dir, entry.name);
                const relativeFilePath = relativePath ? path.join(relativePath, entry.name) : entry.name;

                if (entry.isDirectory()) {
                    findGroundtruthFiles(fullPath, relativeFilePath);
                } else if (entry.isFile() && entry.name.endsWith('.groundtruth.json')) {
                    groundtruthData.files.push({
                        name: entry.name,
                        path: relativeFilePath,
                        directory: relativePath || 'root',
                        type: entry.name.includes('consolidated') ? 'consolidated' : 'individual'
                    });
                }
            }
        }

        findGroundtruthFiles(GROUNDTRUTH_PATH);

        res.json(groundtruthData);
    } catch (error) {
        res.status(500).json({ error: 'Failed to list groundtruth files', details: error.message });
    }
});

// API endpoint to load groundtruth file content
app.get('/api/groundtruth/:filename(*)', (req, res) => {
    try {
        const result = safeReadJson(GROUNDTRUTH_PATH, req.params.filename);
        if (result.error) return res.status(result.status).json({ error: result.error });
        res.json(result.data);
    } catch (error) {
        res.status(500).json({ error: 'Failed to load groundtruth file', details: error.message });
    }
});

// Serve the main application
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

app.listen(PORT, () => {
    console.log(`Gaia Evaluation Visualizer running on http://localhost:${PORT}`);
    console.log(`Experiments path: ${EXPERIMENTS_PATH}`);
    console.log(`Evaluations path: ${EVALUATIONS_PATH}`);
    console.log(`Test data path: ${TEST_DATA_PATH}`);
    console.log(`Groundtruth path: ${GROUNDTRUTH_PATH}`);
    console.log(`Agent outputs path: ${AGENT_OUTPUTS_PATH}`);
    if (SINGLE_AGENT_FILE) {
        console.log(`Single agent file: ${SINGLE_AGENT_FILE}`);
    }
});
