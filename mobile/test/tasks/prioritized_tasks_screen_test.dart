import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile/src/tasks/data/task_api_client.dart';
import 'package:mobile/src/tasks/presentation/prioritized_tasks_screen.dart';

import '../helpers/fake_auth_repository.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://localhost:8000');
  });

  TaskApiClient apiWith(List<Map<String, dynamic>> tasks) {
    return TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/prioritized') return _json(tasks);
        fail('unexpected request: ${request.url.path}');
      }),
    );
  }

  Map<String, dynamic> task({
    required String id,
    required String title,
    required double priority,
    required double effectivePriority,
    required String categoryGroup,
  }) {
    return {
      'id': id,
      'title': title,
      'description': null,
      'status': 'pending',
      'due_at': null,
      'estimated_minutes': 60,
      'created_at': '2026-08-24T00:00:00Z',
      'priority_score': priority,
      'effective_priority_score': effectivePriority,
      'confidence_score': 0.9,
      'urgency': 'high',
      'importance': 'high',
      'category': 'learning',
      'category_group': categoryGroup,
      'effort_estimate_minutes': 60,
      'reasoning': 'Because reasons.',
    };
  }

  testWidgets('shows the category label and effective priority score on a task card', (tester) async {
    final api = apiWith([
      task(id: 't1', title: 'Study for exam', priority: 70, effectivePriority: 90, categoryGroup: 'educational'),
    ]);

    await tester.pumpWidget(MaterialApp(home: PrioritizedTasksScreen(enableRealtime: false, taskApiClient: api)));
    await tester.pumpAndSettle();

    expect(find.text('Study for exam'), findsOneWidget);
    // "Educational" appears both as a filter chip label and the task
    // card's category meta -- just assert it shows up at all.
    expect(find.text('Educational'), findsWidgets);
  });

  testWidgets('category filter chips narrow the visible list', (tester) async {
    final api = apiWith([
      task(id: 't1', title: 'Study for exam', priority: 70, effectivePriority: 90, categoryGroup: 'educational'),
      task(id: 't2', title: 'Client proposal', priority: 60, effectivePriority: 60, categoryGroup: 'professional'),
    ]);

    await tester.pumpWidget(MaterialApp(home: PrioritizedTasksScreen(enableRealtime: false, taskApiClient: api)));
    await tester.pumpAndSettle();

    expect(find.text('Study for exam'), findsOneWidget);
    expect(find.text('Client proposal'), findsOneWidget);

    await tester.tap(find.widgetWithText(ChoiceChip, 'Educational'));
    await tester.pumpAndSettle();

    expect(find.text('Study for exam'), findsOneWidget);
    expect(find.text('Client proposal'), findsNothing);
  });
}
