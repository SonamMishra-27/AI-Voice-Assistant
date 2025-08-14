let mediaRecorder;
let recordedChunks = [];
let abortController = null;
let isLLMRunning = false;
let isRecording = false;

// Session ID
function getOrCreateSessionId() {
  const url = new URL(window.location.href);
  let sessionId = url.searchParams.get("session_id");
  if (!sessionId) {
    sessionId = Math.random().toString(36).substring(2, 10);
    url.searchParams.set("session_id", sessionId);
    window.history.replaceState({}, "", url.toString());
  }
  return sessionId;
}
const sessionId = getOrCreateSessionId();

// Elements
const recordBtn = document.getElementById('record-btn');
const stopLLMBtn = document.getElementById('stop-llm');
const audioPlayback = document.getElementById('audio-playback');
const recordingStatus = document.getElementById('recording-status');
const uploadStatus = document.getElementById('upload-status');
const transcriptResult = document.getElementById("transcript-result");
const llmTextResult = document.getElementById("llm-text-result");
const historyItems = document.getElementById("history-items");
const aiThinking = document.getElementById("ai-thinking");

// Disable stop LLM initially
stopLLMBtn.disabled = true;

// Timestamp
function getTimeStamp() {
  return new Date().toLocaleTimeString();
}

// Add to history
function addToHistory(role, text, audio_url = null, timestamp = null) {
  const item = document.createElement("div");
  item.classList.add("history-item");
  const timeStr = timestamp ? new Date(timestamp).toLocaleTimeString() : getTimeStamp();
  let html = `<strong>[${timeStr}] ${role}:</strong><p>${text}</p>`;
  if (audio_url) {
    html += `<audio controls src="${audio_url}" style="margin-top:5px;"></audio>`;
  }
  item.innerHTML = html;
  historyItems.appendChild(item);
  historyItems.scrollTop = historyItems.scrollHeight;
}

// Load chat history
async function loadChatHistory() {
  try {
    const res = await fetch(`/agent/history/${sessionId}`);
    if (!res.ok) throw new Error("Failed to load chat history");
    const data = await res.json();
    historyItems.innerHTML = "";
    (data.history || []).forEach(item => {
      addToHistory(item.role === "user" ? "User" : "AI Response", item.content);
    });
  } catch (err) {
    console.error("Error loading chat history:", err);
  }
}
loadChatHistory();

// =======================
// Reset record button
// =======================
function resetRecordButton() {
  isRecording = false;
  recordBtn.classList.remove('recording');
  recordBtn.innerHTML = '<i class="fas fa-microphone"></i> Start Recording';
  recordingStatus.textContent = "🎙 Not recording";
}

// Reset stop button
function resetStopLLMButton() {
  stopLLMBtn.disabled = true;
  stopLLMBtn.classList.remove("blinking");
  recordingStatus.textContent = "🎙 Not recording / AI idle";
}

// =======================
// Combined Record Button Logic
// =======================
recordBtn.addEventListener('click', async () => {
  if (isLLMRunning) {
    alert("Wait for current AI response or click 'Stop LLM'.");
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    alert("Your browser does not support audio recording.");
    return;
  }

  if (!isRecording) {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream);
      mediaRecorder.start();
      isRecording = true;
      recordBtn.classList.add('recording');
      recordBtn.innerHTML = '<i class="fas fa-stop-circle"></i> Stop Recording';
      recordingStatus.textContent = "🎙 Recording...";
      stopLLMBtn.disabled = true;

      mediaRecorder.ondataavailable = e => {
        if (e.data.size > 0) recordedChunks.push(e.data);
      };

      mediaRecorder.onstop = async () => {
        recordingStatus.textContent = "Processing audio...";
        const audioBlob = new Blob(recordedChunks, { type: "audio/webm" });
        await sendAudioToLLM(audioBlob);
        resetRecordButton();
      };
    } catch (err) {
      alert("Error accessing microphone: " + err.message);
    }

  } else {
    if (mediaRecorder && mediaRecorder.state === "recording") {
      mediaRecorder.stop();
      resetRecordButton();
      recordingStatus.textContent = "Stopping recording...";
    }
  }
});

// Stop LLM
stopLLMBtn.addEventListener("click", () => {
  if (abortController) abortController.abort();
  isLLMRunning = false;
  resetStopLLMButton();

  if (mediaRecorder && mediaRecorder.state === "recording") {
    mediaRecorder.stop();
    resetRecordButton();
  }
  aiThinking.style.display = "none";
});

// =======================
// Send audio to LLM
// =======================
async function sendAudioToLLM(audioBlob) {
  isLLMRunning = true;
  stopLLMBtn.disabled = false;
  stopLLMBtn.classList.add("blinking");
  uploadStatus.textContent = "Uploading audio to AI...";
  transcriptResult.textContent = "";
  llmTextResult.textContent = "";
  audioPlayback.src = "";
  audioPlayback.style.display = "none";
  aiThinking.style.display = "inline";

  abortController = new AbortController();
  const formData = new FormData();
  formData.append("file", audioBlob, "recorded.webm");
  formData.append("voice_id", "en-US-ken");

  try {
    const response = await fetch(`/agent/chat/${sessionId}`, {
      method: "POST",
      body: formData,
      signal: abortController.signal
    });

    if (!response.ok) {
      const errorData = await response.json();
      uploadStatus.textContent = `Error: ${errorData.error || response.statusText}`;
      resetStopLLMButton();
      return;
    }

    const data = await response.json();
    transcriptResult.textContent = data.transcript || "";
    llmTextResult.textContent = data.response_text || "";

    // Add user & AI to history
    addToHistory("User", data.transcript || "");
    addToHistory("AI Response", data.response_text || "", data.audio_url || null);

    // Play audio automatically
    if (data.audio_url) {
      audioPlayback.src = data.audio_url;
      audioPlayback.play().catch(() => {});
      audioPlayback.style.display = "block";
    }

    if (data.history) {
      historyItems.innerHTML = "";
      data.history.forEach(item => {
        addToHistory(item.role === "user" ? "User" : "AI Response", item.content);
      });
    }

  } catch (err) {
    if (err.name === "AbortError") {
      uploadStatus.textContent = "AI response stopped by user.";
    } else {
      uploadStatus.textContent = "Error communicating with AI: " + err.message;
    }
  } finally {
    aiThinking.style.display = "none";
    isLLMRunning = false;
    stopLLMBtn.disabled = true;
    uploadStatus.textContent = "";
    resetStopLLMButton();
  }
}

// =======================
// Clear Chat History
// =======================
const clearHistoryBtn = document.getElementById("clear-history-btn");
clearHistoryBtn.addEventListener("click", async () => {
  if (!confirm("Are you sure you want to delete the entire chat history?")) return;
  try {
    const response = await fetch(`/agent/history/${sessionId}`, { method: "DELETE" });
    if (!response.ok) {
      const errData = await response.json();
      alert("Failed to delete history: " + (errData.error || response.statusText));
      return;
    }
    historyItems.innerHTML = "";
    transcriptResult.textContent = "";
    llmTextResult.textContent = "";
    audioPlayback.src = "";
    alert("Chat history deleted successfully.");
  } catch (err) {
    alert("Error deleting chat history: " + err.message);
  }
});





