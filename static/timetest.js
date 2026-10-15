'use strict';
document.getElementById('copy-script').addEventListener('click', async () => {
  const script = document.getElementById('clock-script');
  const status = document.getElementById('copy-status');
  try {
    await navigator.clipboard.writeText(script.value);
    status.textContent = 'Copied. Paste into a new mIRC Remote script.';
  } catch {
    script.focus(); script.select();
    status.textContent = 'Script selected. Press Ctrl+C to copy.';
  }
});
