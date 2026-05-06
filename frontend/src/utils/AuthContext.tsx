"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";
import { User, Session } from "@supabase/supabase-js";
import { getSupabase } from "./supabase";

interface AuthState {
  user: User | null;
  session: Session | null;
  loading: boolean;
  signUp: (email: string, password: string) => Promise<{ error: string | null }>;
  signIn: (email: string, password: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
  signInWithGoogle: () => Promise<{ error: string | null }>;
  signInWithMagicLink: (email: string) => Promise<{ error: string | null }>;
  resetPassword: (email: string) => Promise<{ error: string | null }>;
  updatePassword: (password: string) => Promise<{ error: string | null }>;
}

const NOT_CONFIGURED = { error: "Auth not configured" } as const;

const AuthContext = createContext<AuthState>({
  user: null,
  session: null,
  loading: false,
  signUp: async () => NOT_CONFIGURED,
  signIn: async () => NOT_CONFIGURED,
  signOut: async () => {},
  signInWithGoogle: async () => NOT_CONFIGURED,
  signInWithMagicLink: async () => NOT_CONFIGURED,
  resetPassword: async () => NOT_CONFIGURED,
  updatePassword: async () => NOT_CONFIGURED,
});

const originIfBrowser = (): string | undefined => {
  if (typeof window === "undefined") return undefined;
  return window.location.origin;
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) {
      setLoading(false);
      return;
    }

    supabase.auth.getSession().then(({ data: { session } }) => {
      setSession(session);
      setUser(session?.user ?? null);
      setLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, session) => {
      setSession(session);
      setUser(session?.user ?? null);
    });

    return () => subscription.unsubscribe();
  }, []);

  const signUp = async (email: string, password: string) => {
    const supabase = getSupabase();
    if (!supabase) return NOT_CONFIGURED;
    const { error } = await supabase.auth.signUp({
      email,
      password,
      options: { emailRedirectTo: originIfBrowser() },
    });
    return { error: error?.message ?? null };
  };

  const signIn = async (email: string, password: string) => {
    const supabase = getSupabase();
    if (!supabase) return NOT_CONFIGURED;
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    return { error: error?.message ?? null };
  };

  const signOut = async () => {
    await getSupabase()?.auth.signOut();
  };

  const signInWithGoogle = async () => {
    const supabase = getSupabase();
    if (!supabase) return NOT_CONFIGURED;
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: originIfBrowser() },
    });
    // Note: signInWithOAuth navigates the page on success — code after this
    // line generally doesn't run unless `error` is set.
    return { error: error?.message ?? null };
  };

  const signInWithMagicLink = async (email: string) => {
    const supabase = getSupabase();
    if (!supabase) return NOT_CONFIGURED;
    const { error } = await supabase.auth.signInWithOtp({
      email,
      options: { emailRedirectTo: originIfBrowser() },
    });
    return { error: error?.message ?? null };
  };

  const resetPassword = async (email: string) => {
    const supabase = getSupabase();
    if (!supabase) return NOT_CONFIGURED;
    const origin = originIfBrowser();
    const { error } = await supabase.auth.resetPasswordForEmail(email, {
      redirectTo: origin ? `${origin}/reset-password` : undefined,
    });
    return { error: error?.message ?? null };
  };

  const updatePassword = async (password: string) => {
    const supabase = getSupabase();
    if (!supabase) return NOT_CONFIGURED;
    const { error } = await supabase.auth.updateUser({ password });
    return { error: error?.message ?? null };
  };

  return (
    <AuthContext.Provider value={{
      user, session, loading,
      signUp, signIn, signOut,
      signInWithGoogle, signInWithMagicLink, resetPassword, updatePassword,
    }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
