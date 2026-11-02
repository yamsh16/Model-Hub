// Browser-side leftovers that belong to one account. They are wiped whenever the signed-in
// account changes, so nothing from one user is ever shown to the next.

export const WORKSPACE_KEY = "modelhub-workspace"; // sessionStorage: current test set / log / evaluation
export const CHAT_KEY_PREFIX = "modelhub-chat-"; // localStorage: one chat history per endpoint

export function clearAccountData(): void {
  try {
    sessionStorage.removeItem(WORKSPACE_KEY);
    Object.keys(localStorage)
      .filter((k) => k.startsWith(CHAT_KEY_PREFIX))
      .forEach((k) => localStorage.removeItem(k));
  } catch {
    // storage blocked: nothing was saved
  }
}
