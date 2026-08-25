import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/src/onboarding/presentation/onboarding_screen.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('shows the first page and advances through Continue', (tester) async {
    var done = false;
    await tester.pumpWidget(MaterialApp(home: OnboardingScreen(onDone: () => done = true)));

    expect(find.text('AI-powered planning'), findsOneWidget);
    expect(find.text('Continue'), findsOneWidget);

    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(find.text('Works with your real Google Calendar'), findsOneWidget);

    await tester.tap(find.text('Continue'));
    await tester.pumpAndSettle();
    expect(find.text('Turns tasks into an optimized day'), findsOneWidget);
    expect(find.text('Get started'), findsOneWidget);

    await tester.tap(find.text('Get started'));
    await tester.pumpAndSettle();
    expect(done, isTrue);
  });

  testWidgets('Skip completes onboarding immediately', (tester) async {
    var done = false;
    await tester.pumpWidget(MaterialApp(home: OnboardingScreen(onDone: () => done = true)));

    await tester.tap(find.text('Skip'));
    await tester.pumpAndSettle();
    expect(done, isTrue);
  });
}
