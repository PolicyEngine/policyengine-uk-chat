import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

function slugifyBranchName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
}

export function getBackendUrl(): string {
  const vercelEnv = process.env.VERCEL_ENV;
  const gitRef = process.env.VERCEL_GIT_COMMIT_REF;
  if (vercelEnv === "preview" && gitRef) {
    const branchSlug = slugifyBranchName(gitRef);
    return `https://policyengine--peukchat-${branchSlug}-web.modal.run`;
  }

  if (process.env.BACKEND_URL) {
    return process.env.BACKEND_URL;
  }

  if (vercelEnv === "production") {
    // BACKEND_URL must be set in production; falling back to localhost here
    // means every API call fails inside the serverless function with a
    // generic 500. Log loudly so the misconfiguration is diagnosable.
    console.error("BACKEND_URL is not set in production; proxy will fail.");
  }

  return "http://localhost:8080";
}

// Request headers to forward to the backend. The backend keys billing and
// rate limiting on X-User-Id, so dropping it (as the old proxy did) silently
// broke per-user accounting on every request that went through the proxy.
const FORWARDED_HEADERS = ["x-user-id", "authorization"];

export async function GET(request: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  return handleRequest(request, params, "GET");
}
export async function POST(request: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  return handleRequest(request, params, "POST");
}
export async function PUT(request: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  return handleRequest(request, params, "PUT");
}
export async function PATCH(request: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  return handleRequest(request, params, "PATCH");
}
export async function DELETE(request: NextRequest, { params }: { params: Promise<{ slug: string[] }> }) {
  return handleRequest(request, params, "DELETE");
}

async function handleRequest(
  request: NextRequest,
  params: Promise<{ slug: string[] }>,
  method: string,
) {
  const { slug } = await params;
  if (!slug || !Array.isArray(slug)) {
    return NextResponse.json({ error: "Invalid endpoint path" }, { status: 400 });
  }

  const backendUrl = getBackendUrl();
  const url = new URL(`${backendUrl}/${slug.join("/")}`);
  request.nextUrl.searchParams.forEach((value, key) => url.searchParams.append(key, value));

  const forwardHeaders: Record<string, string> = { "Content-Type": "application/json" };
  for (const name of FORWARDED_HEADERS) {
    const value = request.headers.get(name);
    if (value) forwardHeaders[name] = value;
  }

  const fetchOptions: RequestInit = { method, headers: forwardHeaders, redirect: "follow" };
  if (["POST", "PUT", "PATCH"].includes(method)) {
    try {
      const body = await request.text();
      if (body) fetchOptions.body = body;
    } catch {}
  }

  try {
    const response = await fetch(url.toString(), fetchOptions);
    if (!response.ok) {
      // Pass the backend's own error body and status straight through so the
      // client sees the real message (e.g. "No credit remaining") rather than a
      // generic "Backend error: 402", and preserve Retry-After for 429s.
      const errorText = await response.text();
      const passHeaders = new Headers();
      const errCt = response.headers.get("content-type");
      if (errCt) passHeaders.set("content-type", errCt);
      const retryAfter = response.headers.get("retry-after");
      if (retryAfter) passHeaders.set("retry-after", retryAfter);
      return new NextResponse(errorText, { status: response.status, headers: passHeaders });
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("text/event-stream")) {
      // Pipe through a TransformStream to prevent Next.js from buffering SSE
      const { readable, writable } = new TransformStream();
      // The client can disconnect mid-stream; swallow the resulting pipe
      // rejection so it doesn't surface as an unhandled rejection.
      response.body?.pipeTo(writable).catch(() => {});
      return new NextResponse(readable, {
        headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", "Connection": "keep-alive", "X-Accel-Buffering": "no" },
      });
    }
    if (response.status === 204 || response.headers.get("content-length") === "0") {
      return new NextResponse(null, { status: response.status });
    }
    return NextResponse.json(await response.json());
  } catch (error) {
    return NextResponse.json({ error: "Failed to fetch from backend", details: error instanceof Error ? error.message : "Unknown" }, { status: 500 });
  }
}
