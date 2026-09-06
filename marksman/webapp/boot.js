// Local-server build: nothing to boot.
//
// The page loads this before its own script so that the installable build can
// install window.MARKSMAN_BACKEND here and run the engine in the browser. When
// a real Python is already answering over HTTP there is nothing to install, and
// the page falls back to fetch() on its own. The file exists so both builds
// request the same URL.
