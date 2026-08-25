import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/src/auth/auth_repository.dart';
import 'package:mobile/src/auth/presentation/sign_in_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// Overrides every network-touching method so these tests never hit real
/// Supabase -- exercises SignInScreen's own state machine (validation,
/// mode toggle, success/error banners) in isolation.
class _FakeAuthRepository extends AuthRepository {
  _FakeAuthRepository({this.signInError, this.signUpShouldSucceed = true});

  final AuthException? signInError;
  final bool signUpShouldSucceed;
  bool signInCalled = false;
  bool signUpCalled = false;
  bool resetCalled = false;

  @override
  Future<AuthResponse> signInWithEmail({required String email, required String password}) async {
    signInCalled = true;
    if (signInError != null) throw signInError!;
    return AuthResponse();
  }

  @override
  Future<AuthResponse> signUpWithEmail({required String email, required String password, String? fullName}) async {
    signUpCalled = true;
    if (!signUpShouldSucceed) {
      throw AuthException('Email already registered');
    }
    return AuthResponse();
  }

  @override
  Future<void> sendPasswordResetEmail(String email) async {
    resetCalled = true;
  }
}

void main() {
  setUpAll(() async {
    SharedPreferences.setMockInitialValues({});
    await Supabase.initialize(url: 'https://test-project.supabase.co', publishableKey: 'test-anon-key');
  });

  testWidgets('shows a validation error for an invalid email', (tester) async {
    final fake = _FakeAuthRepository();
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'not-an-email');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'password123');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await tester.pump();

    expect(find.text('Enter a valid email'), findsOneWidget);
    expect(fake.signInCalled, isFalse);
  });

  testWidgets('shows a validation error for a short password', (tester) async {
    final fake = _FakeAuthRepository();
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'demo@example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), '123');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await tester.pump();

    expect(find.text('At least 6 characters'), findsOneWidget);
    expect(fake.signInCalled, isFalse);
  });

  testWidgets('valid sign-in submits via the auth repository', (tester) async {
    final fake = _FakeAuthRepository();
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'demo@example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'password123');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await tester.pump();
    await tester.pump();

    expect(fake.signInCalled, isTrue);
  });

  testWidgets('a failed sign-in shows the error banner', (tester) async {
    final fake = _FakeAuthRepository(signInError: AuthException('Invalid login credentials'));
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'demo@example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'password123');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign in'));
    await tester.pump();
    await tester.pump();

    expect(find.text('Invalid login credentials'), findsOneWidget);
  });

  testWidgets('toggling to sign-up mode changes labels and calls sign up', (tester) async {
    final fake = _FakeAuthRepository();
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.tap(find.text("Don't have an account? Sign up"));
    await tester.pump();
    expect(find.text('Create your account'), findsOneWidget);

    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'new@example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'password123');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
    await tester.pump();
    await tester.pump();

    expect(fake.signUpCalled, isTrue);
    expect(find.textContaining('Check your inbox'), findsOneWidget);
  });

  testWidgets('a failed sign-up shows the error banner', (tester) async {
    final fake = _FakeAuthRepository(signUpShouldSucceed: false);
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.tap(find.text("Don't have an account? Sign up"));
    await tester.pump();
    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'taken@example.com');
    await tester.enterText(find.widgetWithText(TextFormField, 'Password'), 'password123');
    await tester.tap(find.widgetWithText(FilledButton, 'Sign up'));
    await tester.pump();
    await tester.pump();

    expect(fake.signUpCalled, isTrue);
    expect(find.text('Email already registered'), findsOneWidget);
  });

  testWidgets('forgot password sends a reset email', (tester) async {
    final fake = _FakeAuthRepository();
    await tester.pumpWidget(MaterialApp(home: SignInScreen(authRepository: fake)));

    await tester.enterText(find.widgetWithText(TextFormField, 'Email'), 'demo@example.com');
    await tester.tap(find.text('Forgot password?'));
    await tester.pump();

    expect(fake.resetCalled, isTrue);
    expect(find.textContaining('Password reset email sent'), findsOneWidget);
  });
}
