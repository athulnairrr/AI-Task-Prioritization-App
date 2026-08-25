import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile/src/scheduling/data/schedule_api_client.dart';
import 'package:mobile/src/tasks/data/task_api_client.dart';
import 'package:mobile/src/tasks/presentation/task_detail_screen.dart';

import '../helpers/fake_auth_repository.dart';

Map<String, dynamic> _taskJson({String status = 'pending'}) => {
      'id': 'task-1',
      'tenant_id': 'tenant-1',
      'created_by': 'user-1',
      'title': 'Finish the client proposal',
      'description': 'Send the final draft to legal first.',
      'raw_input': null,
      'status': status,
      'due_at': '2026-08-30T00:00:00Z',
      'estimated_minutes': 120,
      'created_at': '2026-08-24T00:00:00Z',
      'updated_at': '2026-08-24T00:00:00Z',
    };

http.Response _json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://localhost:8000');
  });

  testWidgets('shows task details and the AI result when already prioritized', (tester) async {
    final taskApi = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/task-1') return _json(_taskJson());
        if (request.url.path == '/tasks/task-1/ai-result') {
          return _json({
            'id': 'ai-1',
            'task_id': 'task-1',
            'model': 'gemini-3.1-flash-lite',
            'category': 'work',
            'urgency': 'high',
            'importance': 'high',
            'priority_score': 94.0,
            'confidence_score': 0.9,
            'effort_estimate_minutes': 120,
            'reasoning': 'This unblocks the client deal and is due soon.',
            'created_at': '2026-08-24T00:00:00Z',
          });
        }
        fail('unexpected request: ${request.method} ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: TaskDetailScreen(taskId: 'task-1', taskApiClient: taskApi, scheduleApiClient: scheduleApi),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Finish the client proposal'), findsOneWidget);
    expect(find.text('Send the final draft to legal first.'), findsOneWidget);
    expect(find.textContaining('Priority: 94'), findsOneWidget);
    expect(find.textContaining('Confidence: 90%'), findsOneWidget);
    expect(find.text('This unblocks the client deal and is due soon.'), findsOneWidget);
  });

  testWidgets('shows an error state when the task fails to load', (tester) async {
    final taskApi = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json({'detail': 'Task not found.'}, 404)),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: TaskDetailScreen(taskId: 'missing', taskApiClient: taskApi, scheduleApiClient: scheduleApi),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Could not load this task.'), findsOneWidget);
    expect(find.text('Try again'), findsOneWidget);
  });

  testWidgets('Complete calls the API and pops with a result', (tester) async {
    var completeCalled = false;
    final taskApi = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/task-1' && request.method == 'GET') return _json(_taskJson());
        if (request.url.path == '/tasks/task-1/ai-result') return _json({'detail': 'none'}, 404);
        if (request.url.path == '/tasks/task-1/complete') {
          completeCalled = true;
          return _json(_taskJson(status: 'done'));
        }
        fail('unexpected request: ${request.method} ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: TaskDetailScreen(taskId: 'task-1', taskApiClient: taskApi, scheduleApiClient: scheduleApi),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Complete'));
    await tester.pumpAndSettle();

    expect(completeCalled, isTrue);
  });

  testWidgets('shows the scheduled time and Calendar status when applied', (tester) async {
    final taskApi = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/task-1') return _json(_taskJson());
        if (request.url.path == '/tasks/task-1/ai-result') return _json({'detail': 'none'}, 404);
        fail('unexpected request: ${request.method} ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([
            {
              'schedule_item_id': 'si-1',
              'task_id': 'task-1',
              'title': 'Finish the client proposal',
              'starts_at': '2026-08-25T14:00:00Z',
              'ends_at': '2026-08-25T16:00:00Z',
              'status': 'scheduled',
              'needs_attention': false,
              'attention_reason': null,
              'google_event_id': 'evt-1',
              'sync_status': 'synced',
              'priority_score': 94.0,
            }
          ])),
    );

    await tester.pumpWidget(MaterialApp(
      home: TaskDetailScreen(taskId: 'task-1', taskApiClient: taskApi, scheduleApiClient: scheduleApi),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Scheduled'), findsOneWidget);
    expect(find.text('On Google Calendar'), findsOneWidget);
  });
}
