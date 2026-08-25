import 'package:flutter_dotenv/flutter_dotenv.dart';

/// Thin wrapper around the values loaded from `.env` by `flutter_dotenv`.
/// Call [Env.load] once, before [runApp], and read values through here
/// rather than calling `dotenv` directly everywhere.
class Env {
  static Future<void> load() => dotenv.load(fileName: '.env');

  static String get apiBaseUrl => dotenv.get('API_BASE_URL', fallback: 'http://localhost:8000');
  static String get supabaseUrl => dotenv.get('SUPABASE_URL');
  static String get supabaseAnonKey => dotenv.get('SUPABASE_ANON_KEY');
}
