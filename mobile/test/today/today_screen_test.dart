import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile/src/calendar/data/calendar_api_client.dart';
import 'package:mobile/src/scheduling/data/schedule_api_client.dart';
import 'package:mobile/src/tasks/data/task_api_client.dart';
import 'package:mobile/src/today/presentation/today_screen.dart';

import '../helpers/fake_auth_repository.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://localhost:8000');
  });

  testWidgets('still shows the plan when Calendar is not connected (external-events 404)', (tester) async {
    // Regression test: /calendar/external-events genuinely 404s with
    // "Calendar not connected" for a brand-new user -- that must not
    // blow up Future.wait and hide the rest of today's plan. See
    // docs/progress.md "Android release build" for how this was found.
    final taskApi = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/prioritized') {
          return _json([
            {
              'id': 'task-1',
              'title': 'Finish client proposal',
              'description': null,
              'status': 'pending',
              'due_at': null,
              'estimated_minutes': 60,
              'created_at': '2026-08-24T00:00:00Z',
              'priority_score': 90.0,
              'confidence_score': 0.9,
              'urgency': 'high',
              'importance': 'high',
              'category': 'work',
              'effort_estimate_minutes': 60,
              'reasoning': 'Nearest deadline.',
            }
          ]);
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/schedule/items') return _json([]);
        if (request.url.path == '/tasks/schedule/needs-attention') return _json([]);
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/calendar/external-events') {
          return _json({'detail': 'Calendar not connected.'}, 404);
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );

    await tester.pumpWidget(MaterialApp(
      home: TodayScreen(
        enableRealtime: false,
        taskApiClient: taskApi,
        scheduleApiClient: scheduleApi,
        calendarApiClient: calendarApi,
      ),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Finish client proposal'), findsWidgets);
    expect(find.textContaining('Could not load'), findsNothing);
  });
}
