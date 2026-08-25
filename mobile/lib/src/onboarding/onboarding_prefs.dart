import 'package:shared_preferences/shared_preferences.dart';

/// Persists whether the user has already seen the onboarding carousel, so
/// it only shows once (until the app's local storage is cleared).
class OnboardingPrefs {
  static const _key = 'has_seen_onboarding';

  static Future<bool> hasSeenOnboarding() async {
    final prefs = await SharedPreferences.getInstance();
    return prefs.getBool(_key) ?? false;
  }

  static Future<void> markSeen() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setBool(_key, true);
  }
}
