import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, useAuth } from "./AuthContext";
import { getSupabase } from "./supabase";

vi.mock("./supabase", () => ({ getSupabase: vi.fn() }));

function SignUpHarness() {
  const { signUp } = useAuth();
  const [result, setResult] = useState("");
  return (
    <>
      <button
        type="button"
        onClick={async () => setResult(JSON.stringify(await signUp("person@example.com", "password123")))}
      >
        Sign up
      </button>
      <output>{result}</output>
    </>
  );
}

function mockClient(session: object | null) {
  vi.mocked(getSupabase).mockReturnValue({
    auth: {
      getSession: vi.fn().mockResolvedValue({ data: { session: null } }),
      onAuthStateChange: vi.fn().mockReturnValue({
        data: { subscription: { unsubscribe: vi.fn() } },
      }),
      signUp: vi.fn().mockResolvedValue({ data: { session }, error: null }),
    },
  } as never);
}

describe("AuthProvider signup", () => {
  beforeEach(() => vi.clearAllMocks());

  it("reports that email confirmation is required when signup has no session", async () => {
    mockClient(null);
    render(<AuthProvider><SignUpHarness /></AuthProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Sign up" }));

    expect(await screen.findByText(/requiresEmailConfirmation.*true/)).toBeInTheDocument();
  });

  it("reports immediate authentication when signup returns a session", async () => {
    mockClient({ access_token: "token" });
    render(<AuthProvider><SignUpHarness /></AuthProvider>);

    fireEvent.click(screen.getByRole("button", { name: "Sign up" }));

    expect(await screen.findByText(/requiresEmailConfirmation.*false/)).toBeInTheDocument();
  });
});
