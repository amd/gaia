// The fixture's "source". server.js compiles it on start and on every save;
// put a syntax error here to make the server log a build error.
const greeting = "Hello from the fixture dev server";
module.exports = `<h1>${greeting}</h1><p id="status">Built from src/app.js.</p>`;
