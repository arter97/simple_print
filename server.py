#!/usr/bin/env python3
import subprocess
import threading
import base64
import os
import time

from dotenv import load_dotenv
from flask import Flask, render_template_string
from flask_socketio import SocketIO

# pylint: disable=invalid-name,missing-function-docstring,global-statement,broad-exception-caught

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
socketio = SocketIO(app, async_mode="threading")

# URL to open in a new tab after the process exits
TARGET_URL = os.getenv("END_URL")

index_html = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>CAOS Printer</title>
  <script src="https://cdn.socket.io/4.8.1/socket.io.min.js"></script>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      background: #121212;
      color: #eee;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }

    #controls {
      display: flex;
      flex-direction: row;
      align-items: center;
      justify-content: center;
      gap: 1rem;
      margin-bottom: 1rem;
    }

    .btn {
      padding: 0.9rem 1.8rem;
      font-size: 1.05rem;
      border-radius: 999px;
      border: none;
      cursor: pointer;
      background: #1f6feb;
      color: white;
      box-shadow: 0 6px 18px rgba(0,0,0,0.4);
      white-space: nowrap;
      transition: transform 0.05s ease, box-shadow 0.05s ease, background 0.15s ease;
    }
    .btn:hover {
      background: #2f81f7;
    }
    .btn:active {
      transform: translateY(1px);
      box-shadow: 0 3px 10px rgba(0,0,0,0.4);
    }

    #dropZone {
      width: min(90vw, 900px);
      padding: 6rem;
      border-radius: 10px;
      border: 2px dashed #444;
      color: #bbb;
      text-align: center;
      font-size: 0.95rem;
      margin-bottom: 1rem;
      background: rgba(0,0,0,0.4);
      transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease, transform 0.1s ease;
    }

    #dropZone.dragover {
      border-color: #1f6feb;
      background: rgba(31,111,235,0.15);
      color: #fff;
      transform: scale(1.01);
    }

    #dropZone .hint-main {
      font-weight: 600;
      display: block;
      margin-bottom: 0.2rem;
    }
    #dropZone .hint-sub {
      font-size: 0.85rem;
      color: #888;
    }
    #dropZone.dragover .hint-sub {
      color: #ccc;
    }

    #console {
      width: min(90vw, 900px);
      height: 50vh;
      background: #000;
      color: #0f0;
      padding: 1rem;
      border-radius: 8px;
      overflow-y: auto;
      font-family: "SF Mono", Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
      font-size: 0.9rem;
      border: 1px solid #333;
    }
    #console-title {
      margin-bottom: 0.3rem;
      font-size: 0.9rem;
      color: #aaa;
      width: min(90vw, 900px);
    }

    /* Hide the real file input */
    #fileInput {
      display: none;
    }
  </style>
</head>
<body>
  <div id="controls">
    <button id="uploadBtn" class="btn">Print</button>
    <button id="runBtn" class="btn">Scan</button>
  </div>

  <div id="dropZone">
    <span class="hint-main">Drop a file here to print</span>
    <span class="hint-sub">…or click “Print” to choose a file manually</span>
  </div>

  <div id="console-title">Console output</div>
  <pre id="console"></pre>

  <!-- Hidden file input for the upload button -->
  <input type="file" id="fileInput">

  <script>
    const socket = io();
    const runBtn = document.getElementById('runBtn');
    const uploadBtn = document.getElementById('uploadBtn');
    const fileInput = document.getElementById('fileInput');
    const consoleEl = document.getElementById('console');
    const dropZone = document.getElementById('dropZone');

    function appendToConsole(text) {
      consoleEl.textContent += text;
      consoleEl.scrollTop = consoleEl.scrollHeight;
    }

    // ---------- Socket events ----------

    runBtn.addEventListener('click', () => {
      socket.emit('scan');
    });

    uploadBtn.addEventListener('click', () => {
      // Open file picker
      fileInput.value = ""; // reset
      fileInput.click();
    });

    fileInput.addEventListener('change', () => {
      const file = fileInput.files[0];
      if (!file) return;
      sendFileToServer(file);
    });

    socket.on('console_output', (msg) => {
      appendToConsole(msg.data);
    });

    socket.on('console_clear', () => {
      consoleEl.textContent = '';
    });

    // Open URL after process exits
    socket.on('open_url', (msg) => {
      if (msg && msg.url) {
        window.open(msg.url, '_blank');
      }
    });

    // ---------- Drag & Drop handling ----------

    // Prevent default behavior for drag events on window to avoid "open file" navigation
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
      window.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
      }, false);
    });

    // Highlight drop zone on dragover / dragenter
    ['dragenter', 'dragover'].forEach(eventName => {
      dropZone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.add('dragover');
      }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
      dropZone.addEventListener(eventName, (e) => {
        e.preventDefault();
        e.stopPropagation();

        // Make sure we're actually leaving the element, not entering a child
        if (eventName === 'dragleave' && e.target !== dropZone) {
          return;
        }

        dropZone.classList.remove('dragover');
      }, false);
    });

    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.remove('dragover');

      const dt = e.dataTransfer;
      if (!dt || !dt.files || dt.files.length === 0) {
        return;
      }
      const file = dt.files[0];
      sendFileToServer(file);
    });

    // ---------- Upload helper (BINARY SAFE) ----------

    function sendFileToServer(file) {
      const reader = new FileReader();
      reader.onload = () => {
        const arrayBuffer = reader.result;
        const bytes = new Uint8Array(arrayBuffer);

        // Convert to base64
        let binary = "";
        for (let i = 0; i < bytes.length; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        const base64data = btoa(binary);

        socket.emit('print', {
          content_b64: base64data
        });
      };
      // Binary-safe read:
      reader.readAsArrayBuffer(file);
    }
  </script>
</body>
</html>
"""

process_lock = threading.Lock()
current_process = None


@app.route("/")
def index():
    return render_template_string(index_html)


def delayed_remove(path: str, delay: float = 10.0):
    def worker():
        time.sleep(delay)
        try:
            os.remove(path)
        except Exception as e:
            print(f"Error removing {path}: {e}")

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def start_process_and_stream(
    command: list[str], stdin_data: bytes | None = None, welcome_msg: str | None = None, end_file: str | None = None,
):
    """Helper: start process, optionally feed stdin, stream stdout/stderr, then open URL."""
    global current_process

    with process_lock:
        if current_process is not None and current_process.poll() is None:
            socketio.emit(
                "console_output",
                {"data": "[process already running – wait for it to finish]\n"},
            )
            return

        socketio.emit("console_clear")

        if welcome_msg:
            socketio.emit(
                "console_output",
                {"data": f"[{welcome_msg}]\n"},
            )

        try:
            current_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=False,
                bufsize=0,
            )
        except Exception as e:
            socketio.emit(
                "console_output",
                {"data": f"Failed to start command: {e}\n"},
            )
            current_process = None
            return

    def stream_output(proc: subprocess.Popen, stdin: bytes | None, end_file: str | None = None):
        try:
            # Send data to stdin if provided
            if stdin is not None and proc.stdin:
                try:
                    proc.stdin.write(stdin)
                    proc.stdin.close()
                except Exception as e:
                    socketio.emit(
                        "console_output",
                        {"data": f"[error writing to stdin: {e}]\n"},
                    )

            # Stream stdout+stderr as chunks, decoding for display
            if proc.stdout:
                while True:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode("utf-8", errors="replace")
                    socketio.emit("console_output", {"data": text})

            retcode = proc.wait()
            socketio.emit(
                "console_output",
                {"data": f"\n[process exited with code {retcode}]\n"},
            )

            if end_file and os.path.exists(end_file):
                socketio.emit("open_url", {"url": TARGET_URL + end_file})
                delayed_remove(end_file, 30)

        finally:
            global current_process
            with process_lock:
                current_process = None

    t = threading.Thread(target=stream_output, args=(current_process, stdin_data, end_file))
    t.daemon = True
    t.start()


@socketio.on("scan")
def handle_scan():
    command = [
        "bash",
        "-c",
        "unbuffer scanimage --batch=page-%03d.png --format=png --resolution 300 -x 210 -y 297 -d 'airscan:e0:uri' --source ADF && ls page-*.png | parallel convert -quality 80 {} {}.jpg && img2pdf page-*.jpg -o scan.pdf && rm page-*",
    ]
    start_process_and_stream(stdin_data=None, command=command, welcome_msg="Beginning scan", end_file="scan.pdf")


@socketio.on("print")
def handle_print(message):
    command = [
        "lp",
        "-d",
        "caos_printer",
        "-"
    ]
    content_b64 = message.get("content_b64", "")

    try:
        content = base64.b64decode(content_b64)
    except Exception as e:
        socketio.emit(
            "console_output",
            {"data": f"[failed to decode uploaded file: {e}]\n"},
        )
        return

    start_process_and_stream(stdin_data=content, command=command, welcome_msg="Beginning print")


if __name__ == "__main__":
    # pip install flask flask-socketio
    socketio.run(app, host=os.getenv("HOST"), port=int(os.getenv("PORT")), debug=False, allow_unsafe_werkzeug=True)
