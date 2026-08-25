"use client";

import { useState } from "react";
import { createClient } from "@/lib/supabase/client";

/**
 * Deliberately minimal: enough to exercise signup/login for real so the
 * task screens are reachable. Not the polished auth UI -- that's a later
 * phase (see /docs/progress.md).
 */
export function AuthForm() {
  const supabase = createClient();
  const [isSignUp, setIsSignUp] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setInfo(null);
    try {
      if (isSignUp) {
        const { error } = await supabase.auth.signUp({ email, password });
        if (error) throw error;
        setInfo("Check your email to confirm your account, then sign in.");
      } else {
        const { error } = await supabase.auth.signInWithPassword({ email, password });
        if (error) throw error;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleForgotPassword() {
    if (!email) {
      setError("Enter your email above first.");
      return;
    }
    const { error } = await supabase.auth.resetPasswordForEmail(email);
    if (error) {
      setError(error.message);
    } else {
      setError(null);
      setInfo("Password reset email sent.");
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: "64px auto", padding: 24 }}>
      <h1 style={{ marginBottom: 24 }}>{isSignUp ? "Create an account" : "Sign in"}</h1>
      {error && <p style={{ color: "#c0392b" }}>{error}</p>}
      {info && <p style={{ color: "#2563eb" }}>{info}</p>}
      <form onSubmit={handleSubmit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <label>
          Email
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={inputStyle}
          />
        </label>
        <label>
          Password
          <input
            type="password"
            required
            minLength={6}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={inputStyle}
          />
        </label>
        <button type="submit" disabled={submitting} style={buttonStyle}>
          {submitting ? "Please wait..." : isSignUp ? "Sign up" : "Sign in"}
        </button>
      </form>
      <button
        type="button"
        onClick={() => {
          setIsSignUp(!isSignUp);
          setError(null);
          setInfo(null);
        }}
        style={linkButtonStyle}
      >
        {isSignUp ? "Already have an account? Sign in" : "Don't have an account? Sign up"}
      </button>
      {!isSignUp && (
        <button type="button" onClick={handleForgotPassword} style={linkButtonStyle}>
          Forgot password?
        </button>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: "block",
  width: "100%",
  padding: 8,
  marginTop: 4,
  boxSizing: "border-box",
};

const buttonStyle: React.CSSProperties = {
  padding: "8px 16px",
  cursor: "pointer",
};

const linkButtonStyle: React.CSSProperties = {
  display: "block",
  marginTop: 8,
  background: "none",
  border: "none",
  color: "#2563eb",
  cursor: "pointer",
  padding: 0,
  textAlign: "left",
};
