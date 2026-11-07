// The "Copy for LLM" button on the API reference pages. Without clipboard access, the button
// stays hidden and the "View as Markdown" link still works.
document.addEventListener("DOMContentLoaded", () => {
  const button = document.querySelector(".ref-copy");
  const link = document.querySelector(".ref-markdown");
  if (!button || !link || !navigator.clipboard) return;

  button.hidden = false;
  const label = button.textContent;
  const show = (text) => {
    button.textContent = text;
    setTimeout(() => {
      button.textContent = label;
    }, 2000);
  };

  button.addEventListener("click", async () => {
    try {
      const response = await fetch(link.href);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      await navigator.clipboard.writeText(await response.text());
      show("Copied");
    } catch {
      show("Couldn't copy");
    }
  });
});
