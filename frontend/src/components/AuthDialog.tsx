"use client";

import { useEffect, useState } from "react";
import type { AuthResult, SignUpResult } from "@/utils/AuthContext";

interface AuthDialogProps {
  open: boolean;
  onClose: () => void;
  signIn: (email: string, password: string) => Promise<AuthResult>;
  signUp: (email: string, password: string) => Promise<SignUpResult>;
}

export default function AuthDialog({
  open,
  onClose,
  signIn,
  signUp,
}: AuthDialogProps) {
  const [mode, setMode] = useState<"signin" | "signup">("signin");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [confirmationEmail, setConfirmationEmail] = useState<string | null>(null);

  useEffect(() => {
    if (open) return;
    setMode("signin");
    setEmail("");
    setPassword("");
    setError(null);
    setSubmitting(false);
    setConfirmationEmail(null);
  }, [open]);

  if (!open) return null;

  const close = () => {
    if (!submitting) onClose();
  };

  return (
    <div
      role="presentation"
      onClick={close}
      style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="auth-dialog-title"
        onClick={(event) => event.stopPropagation()}
        style={{ background: "var(--surface)", color: "var(--text)", padding: "32px", width: "360px", maxWidth: "90vw", borderRadius: "16px", border: "1px solid var(--border)", boxShadow: "0 10px 40px rgba(0,0,0,0.15)" }}
      >
        {confirmationEmail ? (
          <>
            <h2 id="auth-dialog-title" style={{ margin: "0 0 14px", fontSize: "20px", fontWeight: 600 }}>
              Check your email
            </h2>
            <p style={{ margin: "0 0 12px", fontSize: "14px", lineHeight: 1.6, color: "var(--text-2)" }}>
              We sent a verification link to <strong>{confirmationEmail}</strong>.
            </p>
            <p style={{ margin: "0 0 22px", fontSize: "14px", lineHeight: 1.6, color: "var(--text-2)" }}>
              Verify your email address, then return here to sign in.
            </p>
            <button
              type="button"
              onClick={() => {
                setConfirmationEmail(null);
                setMode("signin");
                setPassword("");
              }}
              style={{ width: "100%", padding: "10px", fontSize: "14px", background: "var(--accent)", color: "var(--accent-fg)", border: "none", cursor: "pointer", fontFamily: "inherit", borderRadius: "8px", fontWeight: 500 }}
            >
              Continue to sign in
            </button>
          </>
        ) : (
          <>
            <h2 id="auth-dialog-title" style={{ margin: "0 0 20px", fontSize: "18px", fontWeight: 600, color: "var(--text)" }}>
              {mode === "signin" ? "Sign in" : "Create account"}
            </h2>
            {error && <div role="alert" style={{ padding: "8px 12px", background: "var(--accent-15)", color: "#ef4444", fontSize: "13px", marginBottom: "16px", borderRadius: "8px" }}>{error}</div>}
            <form
              onSubmit={async (event) => {
                event.preventDefault();
                setSubmitting(true);
                setError(null);
                try {
                  if (mode === "signin") {
                    const result = await signIn(email, password);
                    if (result.error) setError(result.error);
                    else onClose();
                  } else {
                    const result = await signUp(email, password);
                    if (result.error) setError(result.error);
                    else if (result.requiresEmailConfirmation) setConfirmationEmail(email);
                    else onClose();
                  }
                } catch (caught) {
                  setError(caught instanceof Error ? caught.message : "Authentication failed");
                } finally {
                  setSubmitting(false);
                }
              }}
            >
              <input type="email" placeholder="Email" value={email} onChange={(event) => setEmail(event.target.value)} required style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid var(--border)", marginBottom: "10px", fontFamily: "inherit", boxSizing: "border-box", borderRadius: "8px", background: "var(--surface)", color: "var(--text)" }} />
              <input type="password" placeholder="Password" value={password} onChange={(event) => setPassword(event.target.value)} required minLength={6} style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid var(--border)", marginBottom: "16px", fontFamily: "inherit", boxSizing: "border-box", borderRadius: "8px", background: "var(--surface)", color: "var(--text)" }} />
              <button type="submit" disabled={submitting} style={{ width: "100%", padding: "10px", fontSize: "14px", background: "var(--accent)", color: "var(--accent-fg)", border: "none", cursor: submitting ? "not-allowed" : "pointer", fontFamily: "inherit", opacity: submitting ? 0.7 : 1, borderRadius: "8px", fontWeight: 500 }}>
                {submitting ? "..." : mode === "signin" ? "Sign in" : "Create account"}
              </button>
            </form>
            <div style={{ marginTop: "16px", textAlign: "center", fontSize: "13px", color: "var(--muted)" }}>
              {mode === "signin" ? (
                <>No account? <button type="button" onClick={() => { setMode("signup"); setError(null); }} style={{ color: "var(--text)", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textDecoration: "underline" }}>Create one</button></>
              ) : (
                <>Have an account? <button type="button" onClick={() => { setMode("signin"); setError(null); }} style={{ color: "var(--text)", background: "none", border: "none", cursor: "pointer", fontFamily: "inherit", fontSize: "13px", textDecoration: "underline" }}>Sign in</button></>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
