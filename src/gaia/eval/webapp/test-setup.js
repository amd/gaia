const fs = require('fs');
const path = require('path');

console.log('🧪 Testing Gaia Evaluation Visualizer Setup...\n');

// Test 1: Check required files exist
const requiredFiles = [
    'package.json',
    'server.js',
    'public/index.html',
    'public/styles.css',
    'public/app.js',
    'README.md'
];

let allFilesExist = true;
requiredFiles.forEach(file => {
    const filePath = path.join(__dirname, file);
    if (fs.existsSync(filePath)) {
        console.log(`✅ ${file} exists`);
    } else {
        console.log(`❌ ${file} missing`);
        allFilesExist = false;
    }
});

// Test 2: Check data directories
const experimentsPath = path.join(__dirname, '../../../..', 'experiments');
const evaluationsPath = path.join(__dirname, '../../../..', 'evaluation');

console.log('\n📁 Checking data directories:');
if (fs.existsSync(experimentsPath)) {
    const experimentFiles = fs.readdirSync(experimentsPath).filter(f => f.endsWith('.experiment.json'));
    console.log(`✅ Experiments directory: ${experimentFiles.length} files found`);
    experimentFiles.forEach(file => console.log(`   - ${file}`));
} else {
    console.log(`❌ Experiments directory not found: ${experimentsPath}`);
}

if (fs.existsSync(evaluationsPath)) {
    const evaluationFiles = fs.readdirSync(evaluationsPath).filter(f => f.endsWith('.experiment.eval.json'));
    console.log(`✅ Evaluations directory: ${evaluationFiles.length} files found`);
    evaluationFiles.forEach(file => console.log(`   - ${file}`));
} else {
    console.log(`❌ Evaluations directory not found: ${evaluationsPath}`);
}

// Test 3: Check package.json
console.log('\n📦 Checking package.json:');
try {
    const packageJson = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8'));
    console.log(`✅ Package name: ${packageJson.name}`);
    console.log(`✅ Version: ${packageJson.version}`);
    console.log(`✅ Dependencies: ${Object.keys(packageJson.dependencies || {}).join(', ')}`);
} catch (error) {
    console.log(`❌ Error reading package.json: ${error.message}`);
    allFilesExist = false;
}

console.log('\n🚀 Next Steps:');
if (allFilesExist) {
    console.log('1. Run: npm install');
    console.log('2. Run: npm start');
    console.log('3. Open: http://localhost:3000');
    console.log('\n✨ Setup looks good! Ready to visualize evaluation results.');
} else {
    console.log('❌ Some files are missing. Please check the setup.');
}

console.log('\n📖 For detailed instructions, see README.md'); 