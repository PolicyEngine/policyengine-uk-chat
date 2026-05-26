"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/utils/AuthContext";
import { THEME } from "@/components/theme";

/**
 * Landing page for the Supabase password-reset email link.
 *
 * Supabase puts the recovery token in the URL hash and the SDK auto-creates
 * a temporary session. We render a "set new password" form, and on submit
 * call `updateUser({ password })` which finalises the change. The user is
 * already signed in at that point — we just bounce them back to the chat.
 */
export default function ResetPasswordPage() {
  const router = useRouter();
  const { user, loading, updatePassword } = useAuth();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  // The Supabase SDK parses the recovery token from the URL hash asynchronously
  // after createClient runs, then fires an auth-state-change event. Without a
  // grace period the page would briefly render "invalid link" before the
  // session arrives. Wait 600ms before treating "no user" as a failure.
  const [hashGracePassed, setHashGracePassed] = useState(false);

  const ready = !loading && !!user;
  const linkInvalid = !loading && !user && hashGracePassed;

  useEffect(() => {
    const t = setTimeout(() => setHashGracePassed(true), 600);
    return () => clearTimeout(t);
  }, []);

  useEffect(() => {
    if (done) {
      const t = setTimeout(() => router.push("/"), 1500);
      return () => clearTimeout(t);
    }
  }, [done, router]);

  const onSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (password.length < 6) { setError("Password must be at least 6 characters."); return; }
    if (password !== confirm) { setError("Passwords don't match."); return; }
    setSubmitting(true);
    setError(null);
    const { error: err } = await updatePassword(password);
    setSubmitting(false);
    if (err) setError(err);
    else setDone(true);
  };

  return (
    <div style={{ minHeight: "100vh", background: "#fafaf9", display: "flex", alignItems: "center", justifyContent: "center", padding: "20px" }}>
      <div style={{ background: "#fff", padding: "32px", width: "360px", maxWidth: "92vw", border: "1px solid #e5e7eb" }}>
        <h2 style={{ margin: "0 0 8px", fontSize: "18px", fontWeight: 600, color: "#1c1a17" }}>Set a new password</h2>
        <p style={{ margin: "0 0 20px", fontSize: "13px", color: "#6b7280", lineHeight: 1.5 }}>
          You'll be signed in once the new password is saved.
        </p>

        {(loading || (!user && !hashGracePassed)) && (
          <div style={{ fontSize: "13px", color: "#6b7280" }}>Verifying reset link…</div>
        )}

        {linkInvalid && (
          <>
            <div style={{ padding: "10px 12px", background: "#fef2f2", color: "#b91c1c", fontSize: "13px", marginBottom: "12px" }}>
              This reset link is invalid or has expired.
            </div>
            <button
              type="button"
              onClick={() => router.push("/")}
              style={{ width: "100%", padding: "10px", fontSize: "14px", background: THEME.primaryGradient, color: "#fff", border: "none", cursor: "pointer", fontFamily: "inherit" }}
            >
              Back to sign in
            </button>
          </>
        )}

        {ready && !done && (
          <>
            {error && <div style={{ padding: "8px 12px", background: "#fef2f2", color: "#b91c1c", fontSize: "13px", marginBottom: "16px" }}>{error}</div>}
            <form onSubmit={onSubmit}>
              <input
                type="password"
                placeholder="New password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                minLength={6}
                autoFocus
                style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid #e5e7eb", marginBottom: "10px", fontFamily: "inherit", boxSizing: "border-box" }}
              />
              <input
                type="password"
                placeholder="Confirm new password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                minLength={6}
                style={{ width: "100%", padding: "10px 12px", fontSize: "14px", border: "1px solid #e5e7eb", marginBottom: "16px", fontFamily: "inherit", boxSizing: "border-box" }}
              />
              <button
                type="submit"
                disabled={submitting}
                style={{ width: "100%", padding: "10px", fontSize: "14px", background: THEME.primaryGradient, color: "#fff", border: "none", cursor: submitting ? "not-allowed" : "pointer", fontFamily: "inherit", opacity: submitting ? 0.7 : 1 }}
              >
                {submitting ? "Saving…" : "Save new password"}
              </button>
            </form>
          </>
        )}

        {done && (
          <div style={{ padding: "10px 12px", background: "#ecfdf5", color: "#065f46", fontSize: "13px" }}>
            Password updated. Redirecting…
          </div>
        )}
      </div>
    </div>
  );
}
