import 'package:supabase_flutter/supabase_flutter.dart';

import '../core/supabase_client.dart';

/// Foundation-level wrapper around Supabase Auth. No UI here -- screens are
/// built in a later phase on top of this repository.
class AuthRepository {
  AuthRepository({SupabaseClient? client}) : _client = client ?? supabase;

  final SupabaseClient _client;

  /// Current signed-in user, or null if signed out.
  User? get currentUser => _client.auth.currentUser;

  /// Emits every auth state change (signed in, signed out, token refreshed).
  Stream<AuthState> get onAuthStateChange => _client.auth.onAuthStateChange;

  Future<AuthResponse> signUpWithEmail({
    required String email,
    required String password,
    String? fullName,
  }) {
    return _client.auth.signUp(
      email: email,
      password: password,
      data: fullName == null ? null : {'full_name': fullName},
    );
  }

  Future<AuthResponse> signInWithEmail({
    required String email,
    required String password,
  }) {
    return _client.auth.signInWithPassword(email: email, password: password);
  }

  Future<void> sendPasswordResetEmail(String email) {
    return _client.auth.resetPasswordForEmail(email);
  }

  /// Completes a password reset -- called from ResetPasswordScreen after
  /// Supabase has already exchanged the recovery link for a session
  /// (`AuthChangeEvent.passwordRecovery`, watched in AuthGate).
  Future<UserResponse> updatePassword(String newPassword) {
    return _client.auth.updateUser(UserAttributes(password: newPassword));
  }

  Future<void> signOut() {
    return _client.auth.signOut();
  }

  /// The current session's access token (JWT), to send as
  /// `Authorization: Bearer <token>` to the FastAPI backend. Null if signed out.
  String? get accessToken => _client.auth.currentSession?.accessToken;
}
