import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:mobile/src/design/widgets/priority_badge.dart';

void main() {
  group('priorityTierFor', () {
    test('null score is low', () => expect(priorityTierFor(null), PriorityTier.low));
    test('score >= 75 is high', () => expect(priorityTierFor(90), PriorityTier.high));
    test('boundary 75 is high', () => expect(priorityTierFor(75), PriorityTier.high));
    test('score 45-74 is medium', () => expect(priorityTierFor(60), PriorityTier.medium));
    test('score < 45 is low', () => expect(priorityTierFor(10), PriorityTier.low));
  });

  testWidgets('PriorityBadge shows the rounded score', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: PriorityBadge(score: 93.6))),
    );
    expect(find.text('Priority 94'), findsOneWidget);
  });

  testWidgets('PriorityBadge shows a placeholder when not yet prioritized', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: PriorityBadge(score: null))),
    );
    expect(find.text('Not prioritized'), findsOneWidget);
  });
}
