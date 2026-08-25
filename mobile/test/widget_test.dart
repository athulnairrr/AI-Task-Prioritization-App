// Smoke test: with no signed-in session and onboarding already marked
// seen, the app should land on the sign-in screen (AuthGate -> SignInScreen).
// Supabase is initialized with dummy, offline-safe credentials -- no
// network call happens unless a form is actually submitted, which this
// test doesn't do.

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import 'package:mobile/main.dart';

void main() {
  setUpAll(() async {
    SharedPreferences.setMockInitialValues({'has_seen_onboarding': true});
    await Supabase.initialize(
      url: 'https://test-project.supabase.co',
      publishableKey: 'test-anon-key',
    );
  });

  testWidgets('shows the sign-in screen when signed out and onboarding is done', (WidgetTester tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(find.text('Welcome back'), findsOneWidget);
    expect(find.widgetWithText(TextFormField, 'Email'), findsOneWidget);
    expect(find.widgetWithText(TextFormField, 'Password'), findsOneWidget);
  });

  testWidgets('shows onboarding first when it has not been seen yet', (WidgetTester tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const MyApp());
    await tester.pumpAndSettle();

    expect(find.text('AI-powered planning'), findsOneWidget);
    expect(find.text('Sign in'), findsNothing);
  });
}
