import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile/src/calendar/data/calendar_api_client.dart';
import 'package:mobile/src/scheduling/data/schedule_api_client.dart';
import 'package:mobile/src/scheduling/presentation/plan_screen.dart';

import '../helpers/fake_auth_repository.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

final _connectionWithWriteAccess = {
  'status': 'connected',
  'google_account_email': 'demo@gmail.com',
  'calendar_id': 'primary',
  'connected_at': '2026-08-01T00:00:00Z',
  'last_error': null,
  'calendar_timezone': 'America/New_York',
  'has_write_access': true,
  'last_synced_at': '2026-08-24T00:00:00Z',
  'watch_active': true,
};

final _connectionReadOnly = {..._connectionWithWriteAccess, 'has_write_access': false};

final _proposalJson = {
  'horizon_start': '2026-08-24T09:00:00Z',
  'horizon_end': '2026-08-25T00:00:00Z',
  'scheduled': [
    {
      'task_id': 'task-1',
      'title': 'Finish client proposal',
      'start': '2026-08-24T14:00:00Z',
      'end': '2026-08-24T16:00:00Z',
      'priority_score': 94.0,
      'score': 88.0,
      'reason': 'Highest priority task with the nearest deadline.',
    },
  ],
  'unscheduled': [],
};

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://localhost:8000');
  });

  testWidgets('Plan my day shows the proposed schedule with priority and reason', (tester) async {
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/schedule') return _json(_proposalJson);
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json(_connectionWithWriteAccess)),
    );

    await tester.pumpWidget(MaterialApp(
      home: PlanScreen(scheduleApiClient: scheduleApi, calendarApiClient: calendarApi),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Plan my day'));
    await tester.pumpAndSettle();

    expect(find.text('Finish client proposal'), findsOneWidget);
    expect(find.text('Priority 94'), findsOneWidget);
    expect(find.text('Highest priority task with the nearest deadline.'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Apply to Google Calendar'), findsOneWidget);
  });

  testWidgets('prompts to connect Calendar permissions when write access is missing', (tester) async {
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json(_proposalJson)),
    );
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json(_connectionReadOnly)),
    );

    await tester.pumpWidget(MaterialApp(
      home: PlanScreen(scheduleApiClient: scheduleApi, calendarApiClient: calendarApi),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Plan my day'));
    await tester.pumpAndSettle();

    expect(find.widgetWithText(OutlinedButton, 'Connect Calendar permissions'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Apply to Google Calendar'), findsNothing);
  });

  testWidgets('applying shows the created/failed summary', (tester) async {
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/schedule') return _json(_proposalJson);
        if (request.url.path == '/tasks/schedule/apply') {
          return _json({
            'created': 1,
            'already_applied': 0,
            'failed': 0,
            'results': [
              {
                'task_id': 'task-1',
                'status': 'created',
                'google_event_id': 'evt-1',
                'start': '2026-08-24T14:00:00Z',
                'end': '2026-08-24T16:00:00Z',
                'reason': null,
              }
            ],
          });
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json(_connectionWithWriteAccess)),
    );

    await tester.pumpWidget(MaterialApp(
      home: PlanScreen(scheduleApiClient: scheduleApi, calendarApiClient: calendarApi),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Plan my day'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Apply to Google Calendar'));
    await tester.pumpAndSettle();

    expect(find.text('1 task scheduled'), findsOneWidget);
    expect(find.text('Finish client proposal'), findsOneWidget);
  });

  testWidgets('an empty proposal shows a helpful message', (tester) async {
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json({'horizon_start': '2026-08-24T00:00:00Z', 'horizon_end': '2026-08-25T00:00:00Z', 'scheduled': [], 'unscheduled': []})),
    );
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json(_connectionWithWriteAccess)),
    );

    await tester.pumpWidget(MaterialApp(
      home: PlanScreen(scheduleApiClient: scheduleApi, calendarApiClient: calendarApi),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Plan my day'));
    await tester.pumpAndSettle();

    expect(find.textContaining('No unscheduled, prioritized tasks found'), findsOneWidget);
  });

  testWidgets('a proposal error shows the error state', (tester) async {
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json({'detail': 'Calendar not connected.'}, 404)),
    );
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json(_connectionWithWriteAccess)),
    );

    await tester.pumpWidget(MaterialApp(
      home: PlanScreen(scheduleApiClient: scheduleApi, calendarApiClient: calendarApi),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.widgetWithText(FilledButton, 'Plan my day'));
    await tester.pumpAndSettle();

    expect(find.text('Calendar not connected.'), findsOneWidget);
  });
}
