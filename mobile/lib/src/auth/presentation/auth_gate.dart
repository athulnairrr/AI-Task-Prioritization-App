import 'dart:async';

import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../navigation/root_shell.dart';
import '../../onboarding/onboarding_prefs.dart';
import '../../onboarding/presentation/onboarding_screen.dart';
import '../auth_repository.dart';
import 'reset_password_screen.dart';
import 'sign_in_screen.dart';

/// Top-level router: onboarding (first launch only) -> sign in/up -> the
/// signed-in app (RootShell) -- switching live as Supabase Auth state
/// changes (sign in / sign out / token refresh / password recovery).
class AuthGate extends StatefulWidget {
  const AuthGate({super.key});

  @override
  State<AuthGate> createState() => _AuthGateState();
}

class _AuthGateState extends State<AuthGate> {
  final _authRepository = AuthRepository();
  bool? _hasSeenOnboarding;
  bool _passwordRecovery = false;
  StreamSubscription<AuthState>? _authSub;

  @override
  void initState() {
    super.initState();
    OnboardingPrefs.hasSeenOnboarding().then((seen) {
      if (mounted) setState(() => _hasSeenOnboarding = seen);
    });
    // Fires when the user opens a Supabase password-reset link and it's
    // exchanged for a recovery session -- see ResetPasswordScreen for the
    // known deep-link limitation.
    _authSub = _authRepository.onAuthStateChange.listen((state) {
      if (state.event == AuthChangeEvent.passwordRecovery && mounted) {
        setState(() => _passwordRecovery = true);
      }
    });
  }

  @override
  void dispose() {
    _authSub?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final hasSeenOnboarding = _hasSeenOnboarding;
    if (hasSeenOnboarding == null) {
      return const Scaffold(body: Center(child: CircularProgressIndicator()));
    }
    if (!hasSeenOnboarding) {
      return OnboardingScreen(onDone: () => setState(() => _hasSeenOnboarding = true));
    }

    return StreamBuilder<AuthState>(
      stream: _authRepository.onAuthStateChange,
      builder: (context, snapshot) {
        if (_passwordRecovery) {
          return ResetPasswordScreen(onDone: () => setState(() => _passwordRecovery = false));
        }
        final signedIn = _authRepository.currentUser != null;
        return signedIn ? const RootShell() : const SignInScreen();
      },
    );
  }
}
