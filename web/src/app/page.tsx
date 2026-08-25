"use client";

import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase/client";
import { AuthForm } from "@/components/AuthForm";
import { TaskList } from "@/components/TaskList";

export default function HomePage() {
  const supabase = createClient();
  const [session, setSession] = useState<Session | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoaded(true);
    });

    const {
      data: { subscription },
    } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
    });

    return () => subscription.unsubscribe();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!loaded) {
    return (
      <main>
        <p style={{ textAlign: "center", marginTop: 64 }}>Loading…</p>
      </main>
    );
  }

  return <main>{session ? <TaskList /> : <AuthForm />}</main>;
}
