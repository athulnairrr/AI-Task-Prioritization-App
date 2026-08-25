import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile/src/calendar/data/calendar_api_client.dart';
import 'package:mobile/src/calendar/presentation/calendar_screen.dart';
import 'package:mobile/src/scheduling/data/schedule_api_client.dart';

import '../helpers/fake_auth_repository.dart';

http.Response _json(Object body, [int status = 200]) =>
    http.Response(jsonEncode(body), status, headers: {'content-type': 'application/json'});

final _notConnected = {
  'status': 'not_connected',
  'google_account_email': null,
  'calendar_id': null,
  'connected_at': null,
  'last_error': null,
  'calendar_timezone': null,
  'has_write_access': false,
  'last_synced_at': null,
  'watch_active': false,
};

final _connected = {
  'status': 'connected',
  'google_account_email': 'demo@gmail.com',
  'calendar_id': 'primary',
  'connected_at': '2026-08-01T00:00:00Z',
  'last_error': null,
  'calendar_timezone': 'America/New_York',
  'has_write_access': true,
  'last_synced_at': '2026-08-24T08:00:00Z',
  'watch_active': true,
};

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://localhost:8000');
  });

  testWidgets('shows "not connected" and a Connect action', (tester) async {
    // Regression test: the real backend 404s /calendar/external-events
    // when there's no connection yet (not an empty 200) -- this must not
    // fail the whole screen, since it's the expected state for a brand
    // new user who hasn't connected Calendar. See docs/progress.md.
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/calendar/connection') return _json(_notConnected);
        if (request.url.path == '/calendar/external-events') {
          return _json({'detail': 'Calendar not connected.'}, 404);
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: CalendarScreen(calendarApiClient: calendarApi, scheduleApiClient: scheduleApi, enableRealtime: false),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Google Calendar not connected'), findsOneWidget);
    expect(find.widgetWithText(TextButton, 'Connect'), findsOneWidget);
  });

  testWidgets('shows connection + sync status and the day agenda when connected', (tester) async {
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/calendar/connection') return _json(_connected);
        if (request.url.path == '/calendar/external-events') {
          return _json([
            {
              'google_event_id': 'ext-1',
              'title': 'Team meeting',
              'start': '2026-08-24T15:00:00Z',
              'end': '2026-08-24T15:30:00Z',
              'all_day': false,
              'status': 'confirmed',
            }
          ]);
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: CalendarScreen(calendarApiClient: calendarApi, scheduleApiClient: scheduleApi, enableRealtime: false),
    ));
    await tester.pumpAndSettle();

    expect(find.text('Google Calendar connected'), findsOneWidget);
    expect(find.textContaining('Live sync'), findsOneWidget);
    expect(find.text('Team meeting'), findsOneWidget);
    expect(find.text('Calendar event'), findsOneWidget);
  });

  testWidgets('Sync now calls the sync endpoint and refreshes', (tester) async {
    var syncCalled = false;
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/calendar/connection') return _json(_connected);
        if (request.url.path == '/calendar/external-events') return _json([]);
        if (request.url.path == '/calendar/sync') {
          syncCalled = true;
          return _json({
            'synced': true,
            'reason': null,
            'full_resync': false,
            'processed': 1,
            'counts': {},
            'watch_active': true,
            'last_synced_at': '2026-08-24T09:00:00Z',
          });
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: CalendarScreen(calendarApiClient: calendarApi, scheduleApiClient: scheduleApi, enableRealtime: false),
    ));
    await tester.pumpAndSettle();

    await tester.tap(find.byIcon(Icons.sync));
    await tester.pumpAndSettle();

    expect(syncCalled, isTrue);
  });

  testWidgets('shows needs-attention items', (tester) async {
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/calendar/connection') return _json(_connected);
        if (request.url.path == '/calendar/external-events') return _json([]);
        fail('unexpected request: ${request.url.path}');
      }),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        if (request.url.path == '/tasks/schedule/items') return _json([]);
        if (request.url.path == '/tasks/schedule/needs-attention') {
          return _json([
            {
              'task_id': 'task-1',
              'schedule_item_id': 'si-1',
              'title': 'Finish client proposal',
              'reason': 'The Google Calendar event for this task was deleted externally.',
              'starts_at': '2026-08-24T14:00:00Z',
              'ends_at': '2026-08-24T16:00:00Z',
            }
          ]);
        }
        fail('unexpected request: ${request.url.path}');
      }),
    );

    await tester.pumpWidget(MaterialApp(
      home: CalendarScreen(calendarApiClient: calendarApi, scheduleApiClient: scheduleApi, enableRealtime: false),
    ));
    await tester.pumpAndSettle();

    expect(find.textContaining('needs attention'), findsOneWidget);
    expect(find.textContaining('Finish client proposal'), findsOneWidget);
  });

  testWidgets('shows an error state when loading fails', (tester) async {
    final calendarApi = CalendarApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json({'detail': 'boom'}, 500)),
    );
    final scheduleApi = ScheduleApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => _json([])),
    );

    await tester.pumpWidget(MaterialApp(
      home: CalendarScreen(calendarApiClient: calendarApi, scheduleApiClient: scheduleApi, enableRealtime: false),
    ));
    await tester.pumpAndSettle();

    expect(find.textContaining('Could not load your calendar'), findsOneWidget);
  });
}
