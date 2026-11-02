// Runs Python away from the page, so the Stop button works even if the code never finishes.
import { loadPyodide } from "./vendor/pyodide/pyodide.mjs";

let runner;

function writer(type) {
  const decoder = new TextDecoder();
  return (bytes) => {
    postMessage({ type, text: decoder.decode(bytes, { stream: true }) });
    return bytes.length;
  };
}

async function load(wheel) {
  const started = performance.now();
  const pyodide = await loadPyodide({
    indexURL: new URL("./vendor/pyodide/", import.meta.url).href,
  });
  pyodide.setStdout({ write: writer("stdout") });
  pyodide.setStderr({ write: writer("stderr") });
  await pyodide.loadPackage(
    ["httpx", "anyio", new URL(`./vendor/${wheel}`, import.meta.url).href],
    { messageCallback: () => {}, errorCallback: () => {} },
  );
  pyodide.FS.mkdirTree("/playground");
  for (const name of ["simulator.py", "runner.py"]) {
    const response = await fetch(new URL(`./python/${name}`, import.meta.url));
    if (!response.ok) throw new Error(`Couldn't fetch ${name}: ${response.status}`);
    pyodide.FS.writeFile(`/playground/${name}`, await response.text());
  }
  pyodide.runPython("import sys; sys.path.insert(0, '/playground')");
  runner = pyodide.pyimport("runner");
  postMessage({ type: "ready", seconds: (performance.now() - started) / 1000 });
}

onmessage = async ({ data }) => {
  if (data.type === "load") {
    try {
      await load(data.wheel);
    } catch (error) {
      postMessage({ type: "failed", message: String(error) });
    }
  } else if (data.type === "run") {
    const started = performance.now();
    const error = await runner.run(data.code);
    postMessage({ type: "done", error: error ?? null, seconds: (performance.now() - started) / 1000 });
  }
};
