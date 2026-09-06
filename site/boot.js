/* Marksman, installable: the same Python engine, running in the browser.
 *
 * The page is backend-agnostic -- it calls window.MARKSMAN_BACKEND if one is
 * installed, and falls back to fetch() against a local Python server if not.
 * This file installs the WASM backend, so the hosted build needs no server at
 * all: Pyodide runs marksman.webapi, and the data file lives in IndexedDB.
 *
 * ponytail: Pyodide rather than a JavaScript port of the engine. It costs a
 * one-time download, and it buys one implementation of the maths instead of
 * two that quietly disagree. If the download ever matters more than that,
 * the seam to hand-port is BACKEND -- nothing else in the page would change.
 */
(() => {
  "use strict";

  // Pinned: an unpinned CDN version is a silent breakage waiting for a
  // Tuesday. Bump deliberately, and re-test the boot path when you do.
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.28.2/full/";
  const DATA = "/data";
  const DB = DATA + "/marksman_data.json";

  let py = null;
  let overlay = null;

  const CSS = [
    "#boot{position:fixed;inset:0;z-index:99;display:flex;align-items:center;",
    "  justify-content:center;background:#141821;color:#f5ebda;",
    "  font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}",
    ".bootbox{max-width:22rem;padding:0 1.5rem;text-align:center}",
    ".bootbox b{font-size:1.35rem;letter-spacing:.02em}",
    ".bootbox p{color:#9aa7b6;margin:.6rem 0 0}",
    "#boot.bad p{color:#ff9a8a}",
    ".bootdart{width:64px;height:16px;border-radius:8px;margin:0 auto 1rem;",
    "  background:linear-gradient(90deg,#ff7a29 0 36%,#f5ebda 36% 100%);",
    "  animation:bootfly 1.4s ease-in-out infinite}",
    "#boot.bad .bootdart{animation:none;opacity:.4}",
    "@keyframes bootfly{0%,100%{transform:translateX(-14px)}50%{transform:translateX(14px)}}",
    "@media (prefers-reduced-motion:reduce){.bootdart{animation:none}}",
  ].join("");

  // This script runs in <head>, so the body may not exist yet; the overlay is
  // parked on <html> until it does.
  let pending = null;

  function status(text, fatal) {
    if (!overlay) {
      const style = document.createElement("style");
      style.textContent = CSS;
      document.head.appendChild(style);
      overlay = document.createElement("div");
      overlay.id = "boot";
      overlay.innerHTML =
        '<div class="bootbox"><div class="bootdart"></div>' +
        '<b>Marksman</b><p id="bootmsg"></p></div>';
      (document.body || document.documentElement).appendChild(overlay);
    }
    pending = [text, !!fatal];
    const msg = overlay.querySelector("#bootmsg");
    if (msg) msg.textContent = text;
    overlay.classList.toggle("bad", !!fatal);
  }

  function done() { if (overlay) { overlay.remove(); overlay = null; } }

  function loadScript(src) {
    return new Promise((ok, bad) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = ok;
      s.onerror = () => bad(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  // IDBFS keeps the data file in IndexedDB. true = load it, false = save it.
  function syncfs(load) {
    return new Promise((ok, bad) => py.FS.syncfs(load, e => (e ? bad(e) : ok())));
  }

  const SETUP = [
    "import json, os, sys",
    "sys.path.insert(0, '/lib')",
    "os.makedirs('/data/packs', exist_ok=True)",
    "os.environ['MARKSMAN_PACKS'] = '/data/packs'",
    "from marksman import webapi",
    "_store = webapi.Store('" + DB + "')",
    "",
    "def _call(path, body_json):",
    "    try:",
    "        body = json.loads(body_json) if body_json else None",
    "        return json.dumps({'ok': True, 'data': webapi.dispatch(_store, path, body)})",
    "    except webapi._Bad as e:",
    "        return json.dumps({'ok': False, 'error': str(e)})",
    "    except Exception as e:",
    "        return json.dumps({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)})",
    "",
    "def _fetch(path, query_json):",
    "    try:",
    "        mime, data = webapi.download(_store, path, json.loads(query_json))",
    "        return json.dumps({'ok': True, 'mime': mime,",
    "                           'text': data.decode('utf-8')})",
    "    except webapi._Bad as e:",
    "        return json.dumps({'ok': False, 'error': str(e)})",
    "    except Exception as e:",
    "        return json.dumps({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)})",
    "",
    "webapi.__dict__.setdefault('_browser_ready', True)",
  ].join("\n");

  async function boot() {
    status("Starting the engine. First visit downloads it once, then it works offline.");
    await loadScript(PYODIDE + "pyodide.js");
    py = await loadPyodide({ indexURL: PYODIDE });

    status("Opening your data.");
    py.FS.mkdirTree(DATA);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, DATA);
    await syncfs(true);

    status("Loading Marksman.");
    const r = await fetch("marksman.zip");
    if (!r.ok) throw new Error("marksman.zip is missing (HTTP " + r.status + ")");
    py.unpackArchive(await r.arrayBuffer(), "zip", { extractDir: "/lib" });
    await py.runPythonAsync(SETUP);
    done();
    return py;
  }

  const ready = boot().catch(err => {
    status("Could not start: " + (err && err.message ? err.message : err) +
           ". Check your connection and reload -- your saved sessions are safe.", true);
    throw err;
  });

  // Anything that writes has to reach IndexedDB before the tab closes.
  const WRITES = new Set(["/api/session", "/api/tool", "/api/sync", "/api/goal",
                          "/api/session/delete"]);

  function unwrap(raw) {
    const out = JSON.parse(raw);
    if (!out.ok) throw new Error(out.error);
    return out;
  }

  function save(name, mime, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: mime }));
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  window.MARKSMAN_BACKEND = {
    // There is no terminal behind the installed app, so it must not send
    // people to one. Packs ride along with the build.
    packHint: "Packs ship with the app. The command-line version can install " +
              "your own from a JSON file.",

    async api(path, body) {
      await ready;
      const out = unwrap(py.globals.get("_call")(path, body ? JSON.stringify(body) : ""));
      if (WRITES.has(path)) await syncfs(false);
      return out.data;
    },

    async download(path) {
      await ready;
      const out = unwrap(py.globals.get("_fetch")(path, "{}"));
      save("marksman-sessions" + path.slice(6), out.mime, out.text);
    },

    // Opened synchronously: a popup blocker will not wait for the engine.
    async openPage(path) {
      const win = window.open("", "_blank");
      const [name, query] = path.split("?");
      const params = {};
      new URLSearchParams(query || "").forEach((v, k) => (params[k] = [v]));
      try {
        await ready;
        const out = unwrap(py.globals.get("_fetch")(name, JSON.stringify(params)));
        if (win) { win.document.write(out.text); win.document.close(); }
      } catch (err) {
        if (win) win.close();
        throw err;
      }
    },
  };

  document.addEventListener("DOMContentLoaded", () => {
    if (overlay && overlay.parentNode !== document.body) {
      document.body.appendChild(overlay);
      if (pending) status(pending[0], pending[1]);
    }
  });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () =>
      navigator.serviceWorker.register("sw.js").catch(() => {}));
  }
})();
