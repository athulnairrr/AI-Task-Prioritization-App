import { createClient } from "@/lib/supabase/client";

/** Thrown for any non-2xx response from the backend. */
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

/**
 * Sends the current Supabase session's access token to the backend --
 * identity and tenant are derived there from the verified token, never
 * from anything this client asserts.
 */
async function authHeaders(): Promise<HeadersInit> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  if (!session) {
    throw new ApiError(401, "Not signed in.");
  }
  return {
    Authorization: `Bearer ${session.access_token}`,
    "Content-Type": "application/json",
  };
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = { ...(await authHeaders()), ...(init?.headers ?? {}) };
  const resp = await fetch(`${API_BASE_URL}${path}`, { ...init, headers });

  if (!resp.ok) {
    let detail = resp.statusText;
    let code: string | undefined;
    try {
      const body = await resp.json();
      if (body?.detail && typeof body.detail === "object") {
        code = body.detail.code;
        detail = body.detail.message ?? detail;
      } else if (body?.detail) {
        detail = String(body.detail);
      }
    } catch {
      // Body wasn't JSON -- fall back to statusText set above.
    }
    throw new ApiError(resp.status, detail, code);
  }

  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}
