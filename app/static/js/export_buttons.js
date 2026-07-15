function formatSharedExportTimestamp(timestamp) {
  if (!timestamp) {
    return "Last downloaded: --";
  }

  try {
    const date = new Date(timestamp);
    const dateStr = date.toLocaleDateString();
    const timeStr = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `Last downloaded: ${dateStr} ${timeStr}`;
  } catch {
    return "Last downloaded: --";
  }
}

function updateSharedExportControl(button) {
  const exportKey = button?.dataset?.exportKey;
  if (!exportKey) {
    return;
  }

  const container = button.closest(".export-control");
  const timestampLabel = container?.querySelector(".export-timestamp");
  if (!timestampLabel) {
    return;
  }

  const timestamp = window.localStorage.getItem(exportKey);
  timestampLabel.textContent = formatSharedExportTimestamp(timestamp);
  button.title = timestamp ? `Last exported: ${timestamp}` : button.title;
}

function setSharedExportTimestamp(button) {
  const exportKey = button?.dataset?.exportKey;
  if (!exportKey) {
    return;
  }

  const now = new Date().toISOString();
  window.localStorage.setItem(exportKey, now);
  updateSharedExportControl(button);
}

window.updateSharedExportControl = updateSharedExportControl;
window.setSharedExportTimestamp = setSharedExportTimestamp;

document.addEventListener("DOMContentLoaded", () => {
  const exportButtons = document.querySelectorAll(".export-button[data-export-key]");
  exportButtons.forEach((button) => {
    updateSharedExportControl(button);
    if (button.dataset.exportTrack === "manual") {
      return;
    }
    button.addEventListener("click", () => {
      setSharedExportTimestamp(button);
    });
  });
});
