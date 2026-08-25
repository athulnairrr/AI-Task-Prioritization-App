import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_dotenv/flutter_dotenv.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:mobile/src/tasks/data/task_api_client.dart';
import 'package:mobile/src/tasks/presentation/add_work_screen.dart';

import '../helpers/fake_auth_repository.dart';

void main() {
  setUpAll(() {
    dotenv.testLoad(fileInput: 'API_BASE_URL=http://localhost:8000');
  });

  testWidgets('shows an error when submitted empty', (tester) async {
    final api = TaskApiClient(authRepository: FakeAuthRepository(), client: MockClient((_) async {
      fail('should not call the API for empty input');
    }));

    await tester.pumpWidget(MaterialApp(home: AddWorkScreen(taskApiClient: api)));
    await tester.tap(find.widgetWithText(FilledButton, 'Add task'));
    await tester.pump();

    expect(find.text('Tell us what you need to get done.'), findsOneWidget);
  });

  testWidgets('natural-language text becomes the task title on creation', (tester) async {
    final requests = <http.Request>[];
    final taskJson = {
      'id': 'task-1',
      'tenant_id': 'tenant-1',
      'created_by': 'user-1',
      'title': 'Finish the proposal by Friday',
      'description': null,
      'raw_input': null,
      'status': 'pending',
      'due_at': null,
      'estimated_minutes': null,
      'created_at': '2026-08-24T00:00:00Z',
      'updated_at': '2026-08-24T00:00:00Z',
    };
    final api = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((request) async {
        requests.add(request);
        final status = request.method == 'POST' ? 201 : 200;
        return http.Response(jsonEncode(taskJson), status, headers: {'content-type': 'application/json'});
      }),
    );

    await tester.pumpWidget(MaterialApp(home: AddWorkScreen(taskApiClient: api)));
    await tester.enterText(find.byType(TextField).first, 'Finish the proposal by Friday');
    await tester.tap(find.widgetWithText(FilledButton, 'Add task'));
    await tester.pump();

    final createRequest = requests.singleWhere((r) => r.method == 'POST' && r.url.path == '/tasks');
    final body = jsonDecode(createRequest.body) as Map<String, dynamic>;
    expect(body['title'], 'Finish the proposal by Friday');
  });

  testWidgets('tapping an example fills the input', (tester) async {
    final api = TaskApiClient(authRepository: FakeAuthRepository(), client: MockClient((_) async {
      fail('should not call the API just from tapping an example chip');
    }));

    await tester.pumpWidget(MaterialApp(home: AddWorkScreen(taskApiClient: api)));
    await tester.tap(find.text('Study system design for 2 hours'));
    await tester.pump();

    final field = tester.widget<TextField>(find.byType(TextField).first);
    expect(field.controller!.text, 'Study system design for 2 hours');
  });

  testWidgets('a failed creation shows the API error message', (tester) async {
    final api = TaskApiClient(
      authRepository: FakeAuthRepository(),
      client: MockClient((_) async => http.Response(jsonEncode({'detail': 'Title is required.'}), 422)),
    );

    await tester.pumpWidget(MaterialApp(home: AddWorkScreen(taskApiClient: api)));
    await tester.enterText(find.byType(TextField).first, 'Something');
    await tester.tap(find.widgetWithText(FilledButton, 'Add task'));
    await tester.pump();
    await tester.pump();

    expect(find.text('Title is required.'), findsOneWidget);
  });
}
