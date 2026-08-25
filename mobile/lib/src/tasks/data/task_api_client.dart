import 'dart:convert';

import 'package:http/http.dart' as http;

import '../../auth/auth_repository.dart';
import '../../core/env.dart';
import 'task_model.dart';

/// Thrown for any non-2xx response. Carries the status code so callers can
/// distinguish e.g. 404 (not found / not yours) from other failures.
class TaskApiException implements Exception {
  TaskApiException(this.statusCode, this.message);

  final int statusCode;
  final String message;

  @override
  String toString() => 'TaskApiException($statusCode): $message';
}

/// Talks to the FastAPI backend's /tasks routes. Every call sends the
/// caller's Supabase access token -- the backend derives identity and
/// tenant from that token, never from anything this client asserts.
///
/// Takes an injectable [http.Client] (defaulting to a real one) so tests
/// can swap in `package:http/testing.dart`'s `MockClient` instead of
/// hitting a real network -- see test/tasks/.
class TaskApiClient {
  TaskApiClient({AuthRepository? authRepository, http.Client? client})
      : _authRepository = authRepository ?? AuthRepository(),
        _client = client ?? http.Client();

  final AuthRepository _authRepository;
  final http.Client _client;

  Uri _uri(String path, [Map<String, String>? query]) {
    return Uri.parse('${Env.apiBaseUrl}$path').replace(queryParameters: query);
  }

  Map<String, String> get _headers {
    final token = _authRepository.accessToken;
    if (token == null) {
      throw StateError('No authenticated session -- sign in before calling the task API.');
    }
    return {
      'Authorization': 'Bearer $token',
      'Content-Type': 'application/json',
    };
  }

  Never _throwFor(http.Response response) {
    String message = response.body;
    try {
      final decoded = jsonDecode(response.body);
      if (decoded is Map && decoded['detail'] != null) {
        message = decoded['detail'].toString();
      }
    } catch (_) {
      // Body wasn't JSON -- fall back to the raw text set above.
    }
    throw TaskApiException(response.statusCode, message);
  }

  Future<List<Task>> listTasks({TaskStatus? status}) async {
    final resp = await _client.get(
      _uri('/tasks', status == null ? null : {'status': status.toApi()}),
      headers: _headers,
    );
    if (resp.statusCode != 200) _throwFor(resp);
    final decoded = jsonDecode(resp.body) as List<dynamic>;
    return decoded.map((e) => Task.fromJson(e as Map<String, dynamic>)).toList();
  }

  /// Tasks joined with their latest AI result -- one call instead of
  /// fetching every task's ai-result individually. Never calls Gemini.
  Future<List<PrioritizedTask>> listPrioritizedTasks({TaskStatus? status}) async {
    final resp = await _client.get(
      _uri('/tasks/prioritized', status == null ? null : {'status': status.toApi()}),
      headers: _headers,
    );
    if (resp.statusCode != 200) _throwFor(resp);
    final decoded = jsonDecode(resp.body) as List<dynamic>;
    return decoded.map((e) => PrioritizedTask.fromJson(e as Map<String, dynamic>)).toList();
  }

  Future<Task> getTask(String taskId) async {
    final resp = await _client.get(_uri('/tasks/$taskId'), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return Task.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Task> createTask({
    required String title,
    String? description,
    DateTime? dueAt,
    int? estimatedMinutes,
  }) async {
    final resp = await _client.post(
      _uri('/tasks'),
      headers: _headers,
      body: jsonEncode({
        'title': title,
        'description': ?description,
        'due_at': ?dueAt?.toUtc().toIso8601String(),
        'estimated_minutes': ?estimatedMinutes,
      }),
    );
    if (resp.statusCode != 201) _throwFor(resp);
    return Task.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Task> updateTask(
    String taskId, {
    String? title,
    String? description,
    TaskStatus? status,
    DateTime? dueAt,
    bool clearDueAt = false,
    int? estimatedMinutes,
  }) async {
    final body = <String, dynamic>{
      'title': ?title,
      'description': ?description,
      'status': ?status?.toApi(),
      // Tri-state: omit (leave unchanged) / explicit null (clear) / a value --
      // the null-aware `?key: value` shorthand can't express "explicit null",
      // so this one field stays as an if/else.
      if (clearDueAt)
        'due_at': null
      else if (dueAt != null)
        'due_at': dueAt.toUtc().toIso8601String(),
      'estimated_minutes': ?estimatedMinutes,
    };
    final resp = await _client.patch(
      _uri('/tasks/$taskId'),
      headers: _headers,
      body: jsonEncode(body),
    );
    if (resp.statusCode != 200) _throwFor(resp);
    return Task.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<Task> completeTask(String taskId) async {
    final resp = await _client.post(_uri('/tasks/$taskId/complete'), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return Task.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  Future<void> deleteTask(String taskId) async {
    final resp = await _client.delete(_uri('/tasks/$taskId'), headers: _headers);
    if (resp.statusCode != 204) _throwFor(resp);
  }

  /// Explicit, user-triggered AI prioritization. Never called automatically
  /// (not on load, not on refresh) -- only from a direct "Prioritize with
  /// AI" tap, to keep Gemini usage predictable and free-tier-safe.
  Future<TaskAiResult> prioritizeTask(String taskId) async {
    final resp = await _client.post(_uri('/tasks/$taskId/prioritize'), headers: _headers);
    if (resp.statusCode != 200) _throwFor(resp);
    return TaskAiResult.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }

  /// Fetches the most recent AI result, if any -- does not call Gemini.
  /// Returns null if the task hasn't been prioritized yet.
  Future<TaskAiResult?> getLatestAiResult(String taskId) async {
    final resp = await _client.get(_uri('/tasks/$taskId/ai-result'), headers: _headers);
    if (resp.statusCode == 404) return null;
    if (resp.statusCode != 200) _throwFor(resp);
    return TaskAiResult.fromJson(jsonDecode(resp.body) as Map<String, dynamic>);
  }
}
