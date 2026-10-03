export function safeHttpUrl(value) {
  if (!value || value.includes("\\") || value !== value.trim() || /[\u0000-\u0020\u007f]/.test(value)) return null;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:")
      && !url.username && !url.password ? url.href : null;
  } catch {
    return null;
  }
}
