// Runs once per server instance, before it handles a request (Next.js 15
// instrumentation). It removes the compromised eMudhra roots from Node's CA
// list before the /api/proxy route calls the Modal backend; see
// src/utils/compromisedCaRoots.ts for when to delete this file.
export async function register() {
  if (process.env.NEXT_RUNTIME === "nodejs") {
    const { removeCompromisedEmudhraRoots } = await import("./utils/compromisedCaRoots");
    removeCompromisedEmudhraRoots();
  }
}
