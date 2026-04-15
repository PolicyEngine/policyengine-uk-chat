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

function getBackendUrl(): string {
  if (process.env.BACKEND_URL) {
    return process.env.BACKEND_URL;
  }

  const vercelEnv = process.env.VERCEL_ENV;
  const gitRef = process.env.VERCEL_GIT_COMMIT_REF;
  if (vercelEnv === "preview" && gitRef) {
    const branchSlug = slugifyBranchName(gitRef);
    return `https://policyengine--peukchat-${branchSlug}-web.modal.run`;
  }

  return "http://localhost:8080";
}

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

  const fetchOptions: RequestInit = { method, headers: { "Content-Type": "application/json" }, redirect: "follow" };
  if (["POST", "PUT", "PATCH"].includes(method)) {
    try {
      const body = await request.text();
      if (body) fetchOptions.body = body;
    } catch {}
  }

  try {
    const response = await fetch(url.toString(), fetchOptions);
    if (!response.ok) {
      const errorText = await response.text();
      return NextResponse.json({ error: `Backend error: ${response.status}`, details: errorText.substring(0, 200) }, { status: response.status });
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("text/event-stream")) {
      // Pipe through a TransformStream to prevent Next.js from buffering SSE
      const { readable, writable } = new TransformStream();
      response.body?.pipeTo(writable);
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
