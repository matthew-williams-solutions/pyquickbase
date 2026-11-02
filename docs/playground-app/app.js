(() => {
  "use strict";

  const OUTPUT_LIMIT = 200000;
  const $ = (id) => document.getElementById(id);
  const ui = {
    picker: $("example"), run: $("run"), stop: $("stop"), reset: $("reset"), status: $("status"),
    output: $("output"), outputPanel: $("output-panel"), sample: $("sample"),
    tabOutput: $("tab-output"), tabSample: $("tab-sample"),
  };

  let data = null;
  let current = null;
  let worker = null;
  let ready = false;
  let running = false;
  let pendingCode = null;
  let buffer = "";
  let written = 0;
  let flushing = false;
  const edits = new Map();

  const editor = CodeMirror.fromTextArea($("code"), {
    mode: "python",
    lineNumbers: true,
    indentUnit: 4,
    viewportMargin: Infinity,
    extraKeys: {
      Tab: (cm) => (cm.somethingSelected() ? cm.indentSelection("add") : cm.replaceSelection("    ")),
      "Ctrl-Enter": () => ui.run.click(),
      "Cmd-Enter": () => ui.run.click(),
      Esc: () => ui.run.focus(),
    },
  });

  // Follow the docs site's light and dark switch when embedded in it.
  function followDocsTheme() {
    try {
      const body = window.parent.document.body;
      if (window.parent === window || !body) return;
      const apply = () => {
        const scheme = body.getAttribute("data-md-color-scheme");
        document.documentElement.dataset.theme = scheme === "slate" ? "dark" : "light";
        editor.refresh();
      };
      apply();
      new MutationObserver(apply).observe(body, {
        attributes: true,
        attributeFilter: ["data-md-color-scheme"],
      });
    } catch {
      // Not embedded in the docs, so the visitor's system setting applies.
    }
  }

  function setBusy(busy) {
    running = busy;
    ui.run.disabled = busy;
    ui.stop.disabled = !busy;
  }

  function showTab(tab) {
    const output = tab === "output";
    ui.tabOutput.setAttribute("aria-selected", String(output));
    ui.tabSample.setAttribute("aria-selected", String(!output));
    ui.outputPanel.hidden = !output;
    ui.sample.hidden = output;
  }

  function clearOutput() {
    ui.output.textContent = "";
    buffer = "";
    written = 0;
  }

  function flush() {
    flushing = false;
    if (!buffer) return;
    const atBottom =
      ui.outputPanel.scrollHeight - ui.outputPanel.scrollTop - ui.outputPanel.clientHeight < 40;
    ui.output.append(buffer);
    buffer = "";
    if (atBottom) ui.outputPanel.scrollTop = ui.outputPanel.scrollHeight;
  }

  function write(text) {
    written += text.length;
    if (written > OUTPUT_LIMIT) {
      stop("Stopped because the output got too long.");
      return;
    }
    buffer += text;
    if (!flushing) {
      flushing = true;
      requestAnimationFrame(flush);
    }
  }

  function writeError(text) {
    flush();
    const span = document.createElement("span");
    span.className = "error";
    span.textContent = text;
    ui.output.append(span);
  }

  function showSavedOutput() {
    clearOutput();
    const note = document.createElement("span");
    note.className = "note";
    note.textContent = "Saved output from this example. Click Run to run it in your browser.";
    ui.output.append(note, current.output);
    ui.status.textContent = "";
  }

  function startWorker() {
    worker = new Worker("worker.js", { type: "module" });
    ready = false;
    worker.onmessage = ({ data: message }) => {
      if (message.type === "stdout") write(message.text);
      else if (message.type === "stderr") {
        flush();
        writeError(message.text);
      } else if (message.type === "ready") {
        ready = true;
        ui.status.textContent = `Python ready in ${message.seconds.toFixed(1)}s`;
        if (pendingCode !== null) {
          send(pendingCode);
          pendingCode = null;
        }
      } else if (message.type === "done") {
        flush();
        if (message.error) writeError(message.error);
        ui.status.textContent = message.error ? "Finished with an error" : `Finished in ${message.seconds.toFixed(2)}s`;
        setBusy(false);
      } else if (message.type === "failed") fail(message.message);
    };
    worker.onerror = () => fail("The Python worker stopped unexpectedly.");
    worker.postMessage({ type: "load", wheel: data.wheel });
  }

  function stopWorker() {
    if (worker) worker.terminate();
    worker = null;
    ready = false;
    pendingCode = null;
  }

  function fail(detail) {
    console.error(detail);
    stopWorker();
    flush();
    writeError("Python couldn't load here. Reload the page to try again, or try another browser.");
    ui.status.textContent = "Couldn't load";
    setBusy(false);
  }

  function stop(reason) {
    stopWorker();
    flush();
    writeError(`${written ? "\n" : ""}${reason}`);
    ui.status.textContent = "Stopped";
    setBusy(false);
  }

  function send(code) {
    ui.status.textContent = "Running";
    worker.postMessage({ type: "run", code });
  }

  function run() {
    if (running) return;
    showTab("output");
    clearOutput();
    setBusy(true);
    const code = editor.getValue();
    if (ready) {
      send(code);
      return;
    }
    pendingCode = code;
    ui.status.textContent = "Loading Python. The first run takes longest.";
    if (!worker) startWorker();
  }

  function selectExample(id) {
    if (current) edits.set(current.id, editor.getValue());
    current = data.examples.find((example) => example.id === id);
    editor.setValue(edits.get(id) ?? current.code);
    editor.clearHistory();
    ui.picker.value = id;
    if (!running) showSavedOutput(); // Otherwise a run is still writing its output.
  }

  function renderSample() {
    for (const table of data.sample) {
      const heading = document.createElement("h2");
      const id = document.createElement("code");
      id.textContent = table.id;
      heading.append(`${table.name} `, id);

      const element = document.createElement("table");
      const head = element.createTHead().insertRow();
      table.columns.forEach((column, index) => {
        const cell = document.createElement("th");
        const fieldId = document.createElement("small");
        fieldId.textContent = ` (${table.ids[index]})`;
        cell.append(column, fieldId);
        head.append(cell);
      });
      const body = element.createTBody();
      for (const row of table.rows) {
        const line = body.insertRow();
        for (const value of row) {
          line.insertCell().textContent = value === null ? "" : String(value);
        }
      }
      const wrap = document.createElement("div");
      wrap.className = "table-wrap";
      wrap.append(element);
      ui.sample.append(heading, wrap);
    }
  }

  function exampleFromHash() {
    const id = location.hash.slice(1);
    return data.examples.some((example) => example.id === id) ? id : null;
  }

  async function start() {
    const response = await fetch("playground-data.json");
    if (!response.ok) throw new Error(`playground-data.json: ${response.status}`);
    data = await response.json();
    for (const example of data.examples) ui.picker.add(new Option(example.title, example.id));
    renderSample();
    selectExample(exampleFromHash() ?? data.examples[0].id);
    ui.run.disabled = false;
    ui.reset.disabled = false;
  }

  ui.run.addEventListener("click", run);
  ui.stop.addEventListener("click", () => stop("Stopped."));
  ui.reset.addEventListener("click", () => {
    edits.delete(current.id);
    editor.setValue(current.code);
    editor.clearHistory();
    if (!running) showSavedOutput();
  });
  ui.picker.addEventListener("change", () => {
    selectExample(ui.picker.value);
    history.replaceState(null, "", `#${ui.picker.value}`);
  });
  window.addEventListener("hashchange", () => {
    const id = exampleFromHash();
    if (id) selectExample(id);
  });
  ui.tabOutput.addEventListener("click", () => showTab("output"));
  ui.tabSample.addEventListener("click", () => showTab("sample"));

  followDocsTheme();
  start().catch((error) => {
    console.error(error);
    ui.output.textContent = "The playground couldn't load. Reload the page to try again.";
  });
})();
