import 'package:supabase_flutter/supabase_flutter.dart';

import 'env.dart';

/// Initializes the Supabase SDK once at app startup. Only the anon
/// (publishable) key ever lives in this app -- never the service role key.
Future<void> initSupabase() async {
  await Supabase.initialize(
    url: Env.supabaseUrl,
    publishableKey: Env.supabaseAnonKey,
  );
}

/// Convenience accessor for the initialized client.
SupabaseClient get supabase => Supabase.instance.client;
