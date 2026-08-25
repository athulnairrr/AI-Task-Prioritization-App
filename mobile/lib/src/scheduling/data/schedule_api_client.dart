import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../auth/auth_repository.dart';
import '../../core/env.dart';
import 'schedule_models.dart';

class ScheduleApiException implements Exception {
  ScheduleApiException(this.statusCode, this.message, {this.code});

  final int statusCode;
  final String message;
  final String? code;

  bool get isReauthRequired => code == 'REAUTH_REQUIRED';

  @override
  String toString() => 'ScheduleApiException($statusCode): $message';
}

/// Talks to POST /tasks/schedule (propose, writes nothing) and
/// POST /tasks/schedule/apply (writes to Google Calendar -- the only
/// write in this client). The backend independently revalidates every
/// applied item; this client never assumes its own proposal is still
/// accurate by the time the user taps Apply.
///
/// Takes an injectable [http.Client] (defaulting to a real one) so tests
/// can swap in `package:http/testing.dart`'s `MockClient`.
class ScheduleApiClient {
  ScheduleApiClient({AuthRepository? authRepository, http.Client? client})
      : _authRepository = authRepository ?? AuthRepository(),
        _client = client ?? http.Client();

  final AuthRepository _authRepository;
  final http.Client _client;

  Map<String, String> get _headers {
    final token = _authRepository.accessToken;
    if (token == null) {
      throw StateError('No authenticated session -- sign in before calling the schedule API.');
    }
    return {
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  Never _throwFor(http.Response response) {
    String message = response.body;
    String? code;
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map && decoded['detail'] != null) {
        final detail = decoded['detail'];
        if (detail is Map) {
          code = detail['code'] as String?;
          message = (detail['message'] ?? detail).toString();
        } else {
          message = detail.toString();
        }
      }
    } catch (_) {
      // Body wasn't JSON -- fall back to the raw text set above.
    }
    throw ScheduleApiException(response.statusCode, message, code: code);
  }

  /// Proposes a schedule for every unscheduled, prioritized task in the
  /// tenant (task_ids omitted). Never writes to Google Calendar. Omit
  /// `horizonStart`/`horizonEnd` for the backend's default (now..+14d);
  /// "Plan my day" / "Plan my week" pass an explicit, narrower horizon.
  Future<ScheduleProposal> proposeSchedule({DateTime? horizonStart, DateTime? horizonEnd}) async {
    final resp = await _client.post(
      Uri.parse('${Env.apiBaseUrl}/tasks/schedule'),
      headers: _headers,
      body: jsonEncode({
        'horizon_start': ?horizonStart?.toUtc().toIso8601String(),
        'horizon_end': ?horizonEnd?.toUtc().toIso8601String(),
      }),
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return ScheduleProposal.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Applied schedule items overlapping `[start, end)` -- what's actually
  /// on the calendar already, as opposed to a fresh proposal. Pass
  /// `taskId` to narrow to one task's schedule item (task detail screen).
  Future<List<ScheduleItem>> listScheduleItems(DateTime start, DateTime end, {String? taskId}) async {
    final resp = await _client.get(
      Uri.parse('${Env.apiBaseUrl}/tasks/schedule/items').replace(queryParameters: {
        'start': start.toUtc().toIso8601String(),
        'end': end.toUtc().toIso8601String(),
        'task_id': ?taskId,
      }),
      headers: _headers,
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((e) => ScheduleItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }

  /// Applies approved schedule items to Google Calendar. Each item is
  /// (taskId, start, end) as the user last saw it in the proposal -- the
  /// backend revalidates all of it before writing anything.
  Future<ScheduleApplyResult> applySchedule(List<ProposedScheduleItem> items) async {
    final resp = await _client.post(
      Uri.parse('${Env.apiBaseUrl}/tasks/schedule/apply'),
      headers: _headers,
      body: jsonEncode({
        'items': items
            .map((i) => {
                  'task_id': i.taskId,
                  'start': i.start.toUtc().toIso8601String(),
                  'end': i.end.toUtc().toIso8601String(),
                })
            .toList(),
      }),
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return ScheduleApplyResult.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Applies (or moves) a single task to a specific time -- used by the
  /// task detail screen's "Reschedule" action. Same endpoint and same
  /// server-side revalidation as a full batch apply; a single-item batch.
  Future<ScheduleApplyResult> applySingleItem(String taskId, DateTime start, DateTime end) async {
    final resp = await _client.post(
      Uri.parse('${Env.apiBaseUrl}/tasks/schedule/apply'),
      headers: _headers,
      body: jsonEncode({
        'items': [
          {
            'task_id': taskId,
            'start': start.toUtc().toIso8601String(),
            'end': end.toUtc().toIso8601String(),
          }
        ],
      }),
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return ScheduleApplyResult.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Previously-applied schedule items whose Google Calendar event was
  /// deleted externally (Phase 7 two-way sync) -- never auto-recreated;
  /// re-applying the task creates a fresh event and clears this.
  Future<List<NeedsAttentionItem>> listNeedsAttention() async {
    final resp = await _client.get(
      Uri.parse('${Env.apiBaseUrl}/tasks/schedule/needs-attention'),
      headers: _headers,
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return (jsonDecode(resp.body) as List<dynamic>)
        .map((e) => NeedsAttentionItem.fromJson(e as Map<String, dynamic>))
        .toList();
  }
}
